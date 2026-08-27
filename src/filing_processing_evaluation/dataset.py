"""Manifest validation and reproducible SEC filing downloads."""

from __future__ import annotations

import hashlib
import json
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


class DatasetError(Exception):
    """Raised when the dataset definition or an artifact is invalid."""


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
    form_type: str
    is_amendment: bool
    filing_date: str
    period_end_date: str
    accession_number: str
    primary_document: str
    source_url: str
    raw_path: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], line_number: int) -> Filing:
        """Parse and validate a manifest JSON object."""
        expected = set(cls.__dataclass_fields__)
        actual = set(value)
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            raise DatasetError(
                f"manifest line {line_number} has invalid fields; "
                f"missing={missing}, extra={extra}"
            )
        try:
            filing = cls(**value)
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
        if self.form_type not in {"10-K", "10-Q"}:
            raise DatasetError(f"{prefix} has unsupported form_type {self.form_type!r}")
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
        parsed_url = urlparse(self.source_url)
        if parsed_url.scheme != "https" or parsed_url.hostname != "www.sec.gov":
            raise DatasetError(f"{prefix} source_url must use the SEC HTTPS archive")
        path = PurePosixPath(self.raw_path)
        if (
            path.is_absolute()
            or ".." in path.parts
            or not path.parts
            or path.parts[0] != "raw"
        ):
            raise DatasetError(f"{prefix} raw_path must be a safe path below raw/")
        expected_raw_path = f"raw/{self.filing_id}/{self.primary_document}"
        if self.raw_path != expected_raw_path:
            raise DatasetError(
                f"{prefix} raw_path must be raw/<filing_id>/<primary_document>"
            )
        accession_key = self.accession_number.replace("-", "")
        expected_source = (
            "https://www.sec.gov/Archives/edgar/data/"
            f"{int(self.cik)}/{accession_key}/{self.primary_document}"
        )
        if self.source_url != expected_source:
            raise DatasetError(f"{prefix} source_url does not match filing metadata")


@dataclass(frozen=True)
class LockEntry:
    """Recorded integrity information for a downloaded raw artifact."""

    filing_id: str
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


@dataclass(frozen=True)
class ValidationSummary:
    """Counts returned after successful validation."""

    total: int
    by_form: Mapping[str, int]


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
    for attribute in ("filing_id", "accession_number", "raw_path"):
        values = [getattr(entry, attribute) for entry in entries]
        duplicates = sorted(
            value for value, count in Counter(values).items() if count > 1
        )
        if duplicates:
            raise DatasetError(f"duplicate {attribute}: {', '.join(duplicates)}")
    return entries


def load_lock(path: Path) -> dict[str, LockEntry]:
    """Load the optional raw artifact lock file."""
    if not path.exists():
        return {}
    entries = [
        LockEntry.from_mapping(value, line) for line, value in _read_json_lines(path)
    ]
    result = {entry.filing_id: entry for entry in entries}
    if len(result) != len(entries):
        raise DatasetError("raw lock contains duplicate filing IDs")
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
    manifest_path: Path, *, check_raw: bool = False, form_type: str | None = None
) -> ValidationSummary:
    """Validate dataset metadata and, optionally, downloaded artifacts."""
    entries = load_manifest(manifest_path)
    selected = [entry for entry in entries if form_type in {None, entry.form_type}]
    lock = load_lock(manifest_path.parent / "raw.lock.jsonl")
    unknown_locks = sorted(set(lock) - {entry.filing_id for entry in entries})
    if unknown_locks:
        raise DatasetError(
            f"raw lock contains unknown filing IDs: {', '.join(unknown_locks)}"
        )
    if check_raw:
        for entry in selected:
            artifact = manifest_path.parent / entry.raw_path
            expected = lock.get(entry.filing_id)
            if expected is None:
                raise DatasetError(f"no raw lock entry for {entry.filing_id}")
            if not artifact.is_file():
                raise DatasetError(f"raw artifact not found: {artifact}")
            digest, size = _digest(artifact)
            if (digest, size) != (expected.sha256, expected.size_bytes):
                raise DatasetError(f"raw artifact failed integrity check: {artifact}")
    counts = Counter(entry.form_type for entry in selected)
    return ValidationSummary(
        total=len(selected), by_form={"10-K": counts["10-K"], "10-Q": counts["10-Q"]}
    )


def _write_lock(path: Path, entries: Mapping[str, LockEntry]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        for filing_id in sorted(entries):
            output.write(json.dumps(entries[filing_id].__dict__, sort_keys=True) + "\n")
    temporary.replace(path)


def _download(entry: Filing, destination: Path, user_agent: str) -> LockEntry:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    request = Request(
        entry.source_url,
        headers={
            "User-Agent": user_agent,
            "Accept-Encoding": "identity",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urlopen(request, timeout=60) as response, temporary.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        temporary.unlink(missing_ok=True)
        raise DatasetError(f"failed to download {entry.filing_id}: {error}") from error
    digest, size = _digest(temporary)
    if size == 0:
        temporary.unlink(missing_ok=True)
        raise DatasetError(f"downloaded an empty artifact for {entry.filing_id}")
    with temporary.open("rb") as artifact:
        header = artifact.read(64 * 1024).lower()
    if b"<html" not in header:
        temporary.unlink(missing_ok=True)
        raise DatasetError(f"downloaded artifact is not HTML: {entry.filing_id}")
    temporary.replace(destination)
    return LockEntry(
        filing_id=entry.filing_id,
        sha256=digest,
        size_bytes=size,
        retrieved_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )


def download_filings(
    entries: Iterable[Filing],
    *,
    dataset_dir: Path,
    user_agent: str,
    form_type: str | None = None,
    filing_ids: set[str] | None = None,
    delay: float = 0.2,
    force: bool = False,
) -> int:
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
    lock_path = dataset_dir / "raw.lock.jsonl"
    lock = load_lock(lock_path)
    for index, entry in enumerate(selected):
        destination = dataset_dir / entry.raw_path
        expected = lock.get(entry.filing_id)
        if destination.is_file() and not force:
            digest, size = _digest(destination)
            if expected is not None and (digest, size) != (
                expected.sha256,
                expected.size_bytes,
            ):
                raise DatasetError(
                    f"existing artifact failed integrity check: {destination}"
                )
            lock[entry.filing_id] = expected or LockEntry(
                filing_id=entry.filing_id,
                sha256=digest,
                size_bytes=size,
                retrieved_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            )
        else:
            actual = _download(entry, destination, user_agent)
            if expected is not None and (
                actual.sha256,
                actual.size_bytes,
            ) != (expected.sha256, expected.size_bytes):
                destination.unlink(missing_ok=True)
                raise DatasetError(
                    f"downloaded artifact does not match raw lock: {entry.filing_id}"
                )
            lock[entry.filing_id] = actual
        _write_lock(lock_path, lock)
        if index < len(selected) - 1 and delay:
            time.sleep(delay)
    return len(selected)
