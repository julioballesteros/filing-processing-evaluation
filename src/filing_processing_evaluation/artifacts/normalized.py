"""Filesystem persistence for normalized documents and their review manifest."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from filing_processing_evaluation.artifacts.errors import ArtifactError
from filing_processing_evaluation.artifacts.files import sha256_file
from filing_processing_evaluation.normalization.errors import NormalizationError
from filing_processing_evaluation.normalization.models import NormalizedDocument
from filing_processing_evaluation.normalization.schema import SCHEMA_VERSION
from filing_processing_evaluation.normalization.validation import validate_normalized

NORMALIZED_MANIFEST_FIELDS = {
    "filing_id",
    "path",
    "sha256",
    "schema_version",
    "status",
    "reviewed_at",
    "reviewed_by",
}


def write_normalized(document: Mapping[str, Any], path: Path) -> None:
    """Write a normalized document deterministically and atomically."""
    validate_normalized(document)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(document), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_normalized(path: Path) -> NormalizedDocument:
    """Load and semantically validate a normalized document."""
    if not path.is_file():
        raise ArtifactError(f"normalized document not found: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ArtifactError(f"normalized document is invalid JSON: {path}") from error
    if not isinstance(value, dict):
        raise ArtifactError(f"normalized document must be a JSON object: {path}")
    try:
        validate_normalized(value)
    except NormalizationError as error:
        raise ArtifactError(f"{error}: {path}") from error
    return cast(NormalizedDocument, value)


def _load_normalized_manifest(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    entries: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ArtifactError(
                f"normalized manifest line {line_number} is invalid JSON"
            ) from error
        if not isinstance(value, dict) or set(value) != NORMALIZED_MANIFEST_FIELDS:
            raise ArtifactError(
                f"normalized manifest line {line_number} has invalid fields"
            )
        filing_id = value.get("filing_id")
        if not isinstance(filing_id, str) or filing_id in entries:
            raise ArtifactError("normalized manifest filing IDs must be unique")
        status = value.get("status")
        reviewed_at = value.get("reviewed_at")
        reviewed_by = value.get("reviewed_by")
        sha256 = value.get("sha256")
        if (
            value.get("schema_version") != SCHEMA_VERSION
            or status not in {"draft", "reviewed"}
            or not isinstance(value.get("path"), str)
            or not str(value["path"]).startswith("normalized/")
            or not isinstance(sha256, str)
            or len(sha256) != 64
        ):
            raise ArtifactError(
                f"normalized manifest line {line_number} has invalid values"
            )
        if (
            status == "draft" and (reviewed_at is not None or reviewed_by is not None)
        ) or (
            status == "reviewed"
            and (
                not isinstance(reviewed_at, str)
                or not isinstance(reviewed_by, str)
                or not reviewed_by
            )
        ):
            raise ArtifactError(
                f"normalized manifest line {line_number} has invalid review state"
            )
        entries[filing_id] = value
    return entries


def update_normalized_manifest(
    *,
    dataset_dir: Path,
    normalized_path: Path,
    document: Mapping[str, Any],
    reviewer: str | None = None,
    reviewed_at: str | None = None,
) -> Path:
    """Record a normalized artifact hash and review state atomically."""
    try:
        relative_path = normalized_path.resolve().relative_to(dataset_dir.resolve())
    except ValueError as error:
        raise ArtifactError(
            "normalized artifact must be inside the dataset directory"
        ) from error
    if not relative_path.parts or relative_path.parts[0] != "normalized":
        raise ArtifactError("normalized artifact path must be below normalized/")
    if not normalized_path.is_file():
        raise ArtifactError(f"normalized document not found: {normalized_path}")
    filing_id = str(document["filing_id"])
    manifest_path = dataset_dir / "normalized" / "manifest.jsonl"
    entries = _load_normalized_manifest(manifest_path)
    artifact_hash = sha256_file(normalized_path)
    previous = entries.get(filing_id)
    if reviewer is None:
        if (
            previous is not None
            and previous.get("sha256") == artifact_hash
            and previous.get("status") == "reviewed"
        ):
            status = "reviewed"
            entry_reviewer = previous.get("reviewed_by")
            entry_reviewed_at = previous.get("reviewed_at")
        else:
            status = "draft"
            entry_reviewer = None
            entry_reviewed_at = None
    else:
        if not reviewer.strip():
            raise ArtifactError("reviewer must not be empty")
        status = "reviewed"
        entry_reviewer = reviewer.strip()
        entry_reviewed_at = reviewed_at or datetime.now(UTC).isoformat().replace(
            "+00:00", "Z"
        )
    entries[filing_id] = {
        "filing_id": filing_id,
        "path": relative_path.as_posix(),
        "sha256": artifact_hash,
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reviewed_at": entry_reviewed_at,
        "reviewed_by": entry_reviewer,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest_path.with_suffix(".jsonl.tmp")
    temporary.write_text(
        "".join(
            json.dumps(entries[key], sort_keys=True) + "\n" for key in sorted(entries)
        ),
        encoding="utf-8",
    )
    temporary.replace(manifest_path)
    return manifest_path
