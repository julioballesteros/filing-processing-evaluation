"""Manifest validation and reproducible SEC filing downloads."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from filing_processing_evaluation.models import FormType


class DatasetError(Exception):
    """Raised when the dataset definition or an artifact is invalid."""


type ArtifactKey = tuple[str, str]

ARTIFACT_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True)
class RawArtifact:
    """One downloadable document belonging to an SEC filing."""

    artifact_id: str
    sequence: int
    document_type: str
    description: str
    filename: str
    media_type: str
    source_url: str
    raw_path: str

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any], *, line_number: int, index: int
    ) -> RawArtifact:
        """Parse one exhibit declared by a manifest entry."""
        expected = set(cls.__dataclass_fields__)
        if set(value) != expected:
            missing = sorted(expected - set(value))
            extra = sorted(set(value) - expected)
            raise DatasetError(
                f"manifest line {line_number} exhibit {index} has invalid fields; "
                f"missing={missing}, extra={extra}"
            )
        try:
            return cls(**value)
        except TypeError as error:
            raise DatasetError(
                f"manifest line {line_number} exhibit {index}: {error}"
            ) from error

    def validate(self, filing: Filing, *, line_number: int, index: int) -> None:
        """Validate exhibit metadata against its parent filing."""
        prefix = f"manifest line {line_number} ({filing.filing_id}) exhibit {index}"
        string_fields = (
            self.artifact_id,
            self.document_type,
            self.description,
            self.filename,
            self.media_type,
            self.source_url,
            self.raw_path,
        )
        if not all(isinstance(field, str) and field for field in string_fields):
            raise DatasetError(f"{prefix} contains an empty or non-string field")
        if (
            self.artifact_id == "primary"
            or ARTIFACT_ID_PATTERN.fullmatch(self.artifact_id) is None
        ):
            raise DatasetError(f"{prefix} has an invalid artifact_id")
        if not isinstance(self.sequence, int) or self.sequence <= 1:
            raise DatasetError(f"{prefix} sequence must be an integer greater than 1")
        if not self.document_type.startswith("EX-"):
            raise DatasetError(f"{prefix} has an invalid exhibit document_type")
        if self.media_type not in {"text/html", "application/xhtml+xml"}:
            raise DatasetError(f"{prefix} has an unsupported media_type")
        filename = PurePosixPath(self.filename)
        if filename.name != self.filename or self.filename in {".", ".."}:
            raise DatasetError(f"{prefix} filename must be a safe basename")
        filing._validate_artifact_location(
            filename=self.filename,
            source_url=self.source_url,
            raw_path=self.raw_path,
            prefix=prefix,
        )


@dataclass(frozen=True)
class Filing:
    """One immutable filing selected for the benchmark."""

    filing_id: str
    regulator: str
    provider: str
    company_name: str
    cik: str
    ticker: str
    sector: str
    form_type: FormType
    is_amendment: bool
    filing_date: str
    period_end_date: str
    accession_number: str
    primary_document: str
    source_url: str
    raw_path: str
    event_date: str | None = None
    items: tuple[str, ...] = ()
    exhibits: tuple[RawArtifact, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], line_number: int) -> Filing:
        """Parse and validate a manifest JSON object."""
        optional = {"event_date", "items", "exhibits"}
        expected = set(cls.__dataclass_fields__)
        required = expected - optional
        actual = set(value)
        if not required <= actual or not actual <= expected:
            missing = sorted(required - actual)
            extra = sorted(actual - expected)
            raise DatasetError(
                f"manifest line {line_number} has invalid fields; "
                f"missing={missing}, extra={extra}"
            )
        values = dict(value)
        raw_items = values.get("items", [])
        if not isinstance(raw_items, list):
            raise DatasetError(f"manifest line {line_number} items must be an array")
        values["items"] = tuple(raw_items)
        raw_exhibits = values.get("exhibits", [])
        if not isinstance(raw_exhibits, list):
            raise DatasetError(f"manifest line {line_number} exhibits must be an array")
        exhibits: list[RawArtifact] = []
        for index, raw_exhibit in enumerate(raw_exhibits, 1):
            if not isinstance(raw_exhibit, dict):
                raise DatasetError(
                    f"manifest line {line_number} exhibit {index} must be an object"
                )
            exhibits.append(
                RawArtifact.from_mapping(
                    raw_exhibit, line_number=line_number, index=index
                )
            )
        values["exhibits"] = tuple(exhibits)
        values.setdefault("event_date", None)
        try:
            values["form_type"] = FormType(values["form_type"])
        except (TypeError, ValueError) as error:
            raise DatasetError(
                f"manifest line {line_number} ({values.get('filing_id')}) "
                f"has unsupported form_type {values.get('form_type')!r}"
            ) from error
        try:
            filing = cls(**values)
        except TypeError as error:
            raise DatasetError(f"manifest line {line_number}: {error}") from error
        filing._validate(line_number)
        return filing

    def _validate(self, line_number: int) -> None:
        prefix = f"manifest line {line_number} ({self.filing_id})"
        string_fields = (
            self.filing_id,
            self.regulator,
            self.provider,
            self.company_name,
            self.cik,
            self.ticker,
            self.sector,
            self.form_type,
            self.filing_date,
            self.period_end_date,
            self.accession_number,
            self.primary_document,
            self.source_url,
            self.raw_path,
        )
        if not all(isinstance(field, str) and field for field in string_fields):
            raise DatasetError(f"{prefix} contains an empty or non-string field")
        if not isinstance(self.is_amendment, bool):
            raise DatasetError(f"{prefix} is_amendment must be boolean")
        if self.regulator != "sec" or self.provider != "sec-edgar":
            raise DatasetError(f"{prefix} has an unsupported regulator or provider")
        if self.is_amendment:
            raise DatasetError(f"{prefix} unexpectedly selects an amended filing")
        if len(self.cik) != 10 or not self.cik.isdigit():
            raise DatasetError(f"{prefix} CIK must contain exactly 10 digits")
        accession_parts = self.accession_number.split("-")
        if [len(part) for part in accession_parts] != [10, 2, 6] or not all(
            part.isdigit() for part in accession_parts
        ):
            raise DatasetError(f"{prefix} has an invalid accession number")
        if self.filing_id != f"sec-{self.accession_number}":
            raise DatasetError(
                f"{prefix} filing_id must be derived from accession_number"
            )
        for field_name, raw_date in (
            ("filing_date", self.filing_date),
            ("period_end_date", self.period_end_date),
        ):
            try:
                date.fromisoformat(raw_date)
            except ValueError as error:
                raise DatasetError(f"{prefix} has invalid {field_name}") from error
        if self.event_date is not None:
            if not isinstance(self.event_date, str):
                raise DatasetError(f"{prefix} has invalid event_date")
            try:
                date.fromisoformat(self.event_date)
            except ValueError as error:
                raise DatasetError(f"{prefix} has invalid event_date") from error
        if not all(isinstance(item, str) and item for item in self.items):
            raise DatasetError(f"{prefix} items must contain non-empty strings")
        if len(set(self.items)) != len(self.items):
            raise DatasetError(f"{prefix} items must be unique")
        if self.form_type == FormType.EIGHT_K:
            if self.event_date is None:
                raise DatasetError(f"{prefix} 8-K filing requires event_date")
            if "2.02" not in self.items:
                raise DatasetError(f"{prefix} earnings 8-K must include Item 2.02")
            if not self.exhibits:
                raise DatasetError(f"{prefix} earnings 8-K requires an exhibit")
            if not any(
                exhibit.artifact_id == "earnings-release" for exhibit in self.exhibits
            ):
                raise DatasetError(
                    f"{prefix} earnings 8-K requires an earnings-release exhibit"
                )
        self._validate_artifact_location(
            filename=self.primary_document,
            source_url=self.source_url,
            raw_path=self.raw_path,
            prefix=prefix,
        )
        for index, exhibit in enumerate(self.exhibits, 1):
            exhibit.validate(self, line_number=line_number, index=index)
        artifact_ids = [artifact.artifact_id for artifact in self.raw_artifacts]
        if len(set(artifact_ids)) != len(artifact_ids):
            raise DatasetError(f"{prefix} contains duplicate artifact IDs")
        sequences = [artifact.sequence for artifact in self.raw_artifacts]
        if len(set(sequences)) != len(sequences):
            raise DatasetError(f"{prefix} contains duplicate artifact sequences")
        raw_paths = [artifact.raw_path for artifact in self.raw_artifacts]
        if len(set(raw_paths)) != len(raw_paths):
            raise DatasetError(f"{prefix} contains duplicate raw artifact paths")

    def _validate_artifact_location(
        self, *, filename: str, source_url: str, raw_path: str, prefix: str
    ) -> None:
        parsed_url = urlparse(source_url)
        if parsed_url.scheme != "https" or parsed_url.hostname != "www.sec.gov":
            raise DatasetError(f"{prefix} source_url must use the SEC HTTPS archive")
        path = PurePosixPath(raw_path)
        if (
            path.is_absolute()
            or ".." in path.parts
            or not path.parts
            or path.parts[0] != "raw"
        ):
            raise DatasetError(f"{prefix} raw_path must be a safe path below raw/")
        expected_raw_path = f"raw/{self.filing_id}/{filename}"
        if raw_path != expected_raw_path:
            raise DatasetError(f"{prefix} raw_path must be raw/<filing_id>/<filename>")
        accession_key = self.accession_number.replace("-", "")
        expected_source = (
            "https://www.sec.gov/Archives/edgar/data/"
            f"{int(self.cik)}/{accession_key}/{filename}"
        )
        if source_url != expected_source:
            raise DatasetError(f"{prefix} source_url does not match filing metadata")

    @property
    def raw_artifacts(self) -> tuple[RawArtifact, ...]:
        """Return the primary document followed by explicitly selected exhibits."""
        primary = RawArtifact(
            artifact_id="primary",
            sequence=1,
            document_type=self.form_type,
            description="Primary filing document",
            filename=self.primary_document,
            media_type="text/html",
            source_url=self.source_url,
            raw_path=self.raw_path,
        )
        return (primary, *self.exhibits)


@dataclass(frozen=True)
class LockEntry:
    """Recorded integrity information for a downloaded raw artifact."""

    filing_id: str
    artifact_id: str
    sha256: str
    size_bytes: int
    retrieved_at: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], line_number: int) -> LockEntry:
        """Parse a raw lock entry."""
        expected = set(cls.__dataclass_fields__)
        if set(value) != expected:
            raise DatasetError(f"lock line {line_number} has invalid fields")
        try:
            entry = cls(**value)
        except TypeError as error:
            raise DatasetError(f"lock line {line_number}: {error}") from error
        if not isinstance(entry.filing_id, str) or not entry.filing_id:
            raise DatasetError(f"lock line {line_number} has an invalid filing ID")
        if (
            not isinstance(entry.artifact_id, str)
            or ARTIFACT_ID_PATTERN.fullmatch(entry.artifact_id) is None
        ):
            raise DatasetError(f"lock line {line_number} has an invalid artifact ID")
        if (
            not isinstance(entry.sha256, str)
            or len(entry.sha256) != 64
            or any(character not in "0123456789abcdef" for character in entry.sha256)
        ):
            raise DatasetError(f"lock line {line_number} has an invalid SHA-256")
        if not isinstance(entry.size_bytes, int) or entry.size_bytes <= 0:
            raise DatasetError(f"lock line {line_number} has an invalid size")
        try:
            timestamp = datetime.fromisoformat(
                entry.retrieved_at.replace("Z", "+00:00")
            )
        except (AttributeError, ValueError) as error:
            raise DatasetError(
                f"lock line {line_number} has an invalid timestamp"
            ) from error
        if timestamp.tzinfo is None:
            raise DatasetError(
                f"lock line {line_number} timestamp must include a timezone"
            )
        return entry

    @property
    def key(self) -> ArtifactKey:
        """Return the filing-local identity of this locked artifact."""
        return self.filing_id, self.artifact_id


@dataclass(frozen=True)
class ValidationSummary:
    """Counts returned after successful validation."""

    total: int
    by_form: Mapping[FormType, int]


@dataclass(frozen=True)
class DownloadSummary:
    """Counts returned after preparing selected raw artifacts."""

    filings: int
    artifacts: int


def _read_json_lines(path: Path) -> Iterable[tuple[int, Mapping[str, Any]]]:
    if not path.is_file():
        raise DatasetError(f"file not found: {path}")
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise DatasetError(f"{path}:{line_number}: invalid JSON") from error
        if not isinstance(value, dict):
            raise DatasetError(f"{path}:{line_number}: expected a JSON object")
        yield line_number, value


def load_manifest(path: Path) -> list[Filing]:
    """Load the filing manifest and reject duplicate identities or paths."""
    entries = [
        Filing.from_mapping(value, line) for line, value in _read_json_lines(path)
    ]
    if not entries:
        raise DatasetError("manifest must contain at least one filing")
    for attribute in ("filing_id", "accession_number"):
        values = [getattr(entry, attribute) for entry in entries]
        duplicates = sorted(
            value for value, count in Counter(values).items() if count > 1
        )
        if duplicates:
            raise DatasetError(f"duplicate {attribute}: {', '.join(duplicates)}")
    raw_paths = [
        artifact.raw_path for entry in entries for artifact in entry.raw_artifacts
    ]
    duplicate_paths = sorted(
        value for value, count in Counter(raw_paths).items() if count > 1
    )
    if duplicate_paths:
        raise DatasetError(f"duplicate raw_path: {', '.join(duplicate_paths)}")
    return entries


def load_lock(path: Path) -> dict[ArtifactKey, LockEntry]:
    """Load the optional raw artifact lock file."""
    if not path.exists():
        return {}
    entries = [
        LockEntry.from_mapping(value, line) for line, value in _read_json_lines(path)
    ]
    result = {entry.key: entry for entry in entries}
    if len(result) != len(entries):
        raise DatasetError("raw lock contains duplicate artifact IDs")
    return result


def _digest(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as artifact:
        while chunk := artifact.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def validate_dataset(
    manifest_path: Path,
    *,
    check_raw: bool = False,
    form_type: FormType | None = None,
) -> ValidationSummary:
    """Validate dataset metadata and, optionally, downloaded artifacts."""
    entries = load_manifest(manifest_path)
    selected = [entry for entry in entries if form_type in {None, entry.form_type}]
    lock = load_lock(manifest_path.parent / "raw.lock.jsonl")
    declared_artifacts = {
        (entry.filing_id, artifact.artifact_id)
        for entry in entries
        for artifact in entry.raw_artifacts
    }
    unknown_locks = sorted(set(lock) - declared_artifacts)
    if unknown_locks:
        raise DatasetError(
            "raw lock contains unknown artifacts: "
            + ", ".join(
                f"{filing_id}/{artifact_id}" for filing_id, artifact_id in unknown_locks
            )
        )
    if check_raw:
        for entry in selected:
            for raw_artifact in entry.raw_artifacts:
                artifact_path = manifest_path.parent / raw_artifact.raw_path
                key = (entry.filing_id, raw_artifact.artifact_id)
                expected = lock.get(key)
                if expected is None:
                    raise DatasetError(
                        f"no raw lock entry for {entry.filing_id}/"
                        f"{raw_artifact.artifact_id}"
                    )
                if not artifact_path.is_file():
                    raise DatasetError(f"raw artifact not found: {artifact_path}")
                digest, size = _digest(artifact_path)
                if (digest, size) != (expected.sha256, expected.size_bytes):
                    raise DatasetError(
                        f"raw artifact failed integrity check: {artifact_path}"
                    )
    counts = Counter(entry.form_type for entry in selected)
    return ValidationSummary(
        total=len(selected),
        by_form={form: counts[form] for form in FormType},
    )


def _write_lock(path: Path, entries: Mapping[ArtifactKey, LockEntry]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        for key in sorted(entries):
            output.write(json.dumps(entries[key].__dict__, sort_keys=True) + "\n")
    temporary.replace(path)


def _download(
    filing_id: str,
    artifact: RawArtifact,
    destination: Path,
    user_agent: str,
) -> LockEntry:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    request = Request(
        artifact.source_url,
        headers={
            "User-Agent": user_agent,
            "Accept-Encoding": "identity",
            "Accept": artifact.media_type,
        },
    )
    try:
        with urlopen(request, timeout=60) as response, temporary.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        temporary.unlink(missing_ok=True)
        raise DatasetError(
            f"failed to download {filing_id}/{artifact.artifact_id}: {error}"
        ) from error
    digest, size = _digest(temporary)
    if size == 0:
        temporary.unlink(missing_ok=True)
        raise DatasetError(
            f"downloaded an empty artifact for {filing_id}/{artifact.artifact_id}"
        )
    with temporary.open("rb") as downloaded_file:
        header = downloaded_file.read(64 * 1024).lower()
    if b"<html" not in header:
        temporary.unlink(missing_ok=True)
        raise DatasetError(
            f"downloaded artifact is not HTML: {filing_id}/{artifact.artifact_id}"
        )
    temporary.replace(destination)
    return LockEntry(
        filing_id=filing_id,
        artifact_id=artifact.artifact_id,
        sha256=digest,
        size_bytes=size,
        retrieved_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )


def download_filings(
    entries: Iterable[Filing],
    *,
    dataset_dir: Path,
    user_agent: str,
    form_type: FormType | None = None,
    filing_ids: set[str] | None = None,
    delay: float = 0.2,
    force: bool = False,
) -> DownloadSummary:
    """Download selected filings and update the integrity lock atomically."""
    if delay < 0:
        raise DatasetError("delay cannot be negative")
    all_entries = list(entries)
    requested_ids = filing_ids or set()
    known_ids = {entry.filing_id for entry in all_entries}
    unknown_ids = sorted(requested_ids - known_ids)
    if unknown_ids:
        raise DatasetError(f"unknown filing IDs: {', '.join(unknown_ids)}")
    selected = [
        entry
        for entry in all_entries
        if form_type in {None, entry.form_type}
        and (not requested_ids or entry.filing_id in requested_ids)
    ]
    selected_artifacts = [
        (entry, artifact) for entry in selected for artifact in entry.raw_artifacts
    ]
    lock_path = dataset_dir / "raw.lock.jsonl"
    lock = load_lock(lock_path)
    for index, (entry, artifact) in enumerate(selected_artifacts):
        destination = dataset_dir / artifact.raw_path
        key = (entry.filing_id, artifact.artifact_id)
        expected = lock.get(key)
        if destination.is_file() and not force:
            digest, size = _digest(destination)
            if expected is not None and (digest, size) != (
                expected.sha256,
                expected.size_bytes,
            ):
                raise DatasetError(
                    f"existing artifact failed integrity check: {destination}"
                )
            lock[key] = expected or LockEntry(
                filing_id=entry.filing_id,
                artifact_id=artifact.artifact_id,
                sha256=digest,
                size_bytes=size,
                retrieved_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            )
        else:
            actual = _download(entry.filing_id, artifact, destination, user_agent)
            if expected is not None and (
                actual.sha256,
                actual.size_bytes,
            ) != (expected.sha256, expected.size_bytes):
                destination.unlink(missing_ok=True)
                raise DatasetError(
                    "downloaded artifact does not match raw lock: "
                    f"{entry.filing_id}/{artifact.artifact_id}"
                )
            lock[key] = actual
        _write_lock(lock_path, lock)
        if index < len(selected_artifacts) - 1 and delay:
            time.sleep(delay)
    return DownloadSummary(filings=len(selected), artifacts=len(selected_artifacts))
