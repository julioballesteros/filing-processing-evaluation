"""Tests for normalized artifact loading and review-manifest persistence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from filing_processing_evaluation.artifacts import (
    ArtifactError,
    load_normalized,
    update_normalized_manifest,
    write_normalized,
)
from tests.support import normalize_document, prepare_normalization_fixture


def test_normalized_loader_rejects_invalid_shapes(tmp_path: Path) -> None:
    path = tmp_path / "document.json"
    path.write_text("not json")
    with pytest.raises(ArtifactError, match="invalid JSON"):
        load_normalized(path)
    path.write_text("[]")
    with pytest.raises(ArtifactError, match="JSON object"):
        load_normalized(path)
    path.write_text(json.dumps({"schema_version": "0.0.0"}))
    with pytest.raises(ArtifactError, match="unsupported schema"):
        load_normalized(path)


def test_normalized_manifest_tracks_hash_and_review_state(tmp_path: Path) -> None:
    entry, digest, _ = prepare_normalization_fixture(tmp_path)
    document = normalize_document(entry, dataset_dir=tmp_path, expected_sha256=digest)
    normalized_path = tmp_path / "normalized" / f"{entry.filing_id}.json"
    write_normalized(document, normalized_path)

    manifest_path = update_normalized_manifest(
        dataset_dir=tmp_path,
        normalized_path=normalized_path,
        document=document,
    )
    draft = json.loads(manifest_path.read_text())
    assert draft["status"] == "draft"
    assert draft["reviewed_at"] is None
    assert draft["reviewed_by"] is None
    assert draft["path"] == f"normalized/{entry.filing_id}.json"

    update_normalized_manifest(
        dataset_dir=tmp_path,
        normalized_path=normalized_path,
        document=document,
        reviewer="Test Reviewer",
        reviewed_at="2026-08-28T10:00:00Z",
    )
    reviewed = json.loads(manifest_path.read_text())
    assert reviewed["status"] == "reviewed"
    assert reviewed["reviewed_by"] == "Test Reviewer"
    assert reviewed["reviewed_at"] == "2026-08-28T10:00:00Z"

    update_normalized_manifest(
        dataset_dir=tmp_path,
        normalized_path=normalized_path,
        document=document,
    )
    assert json.loads(manifest_path.read_text())["status"] == "reviewed"


def test_normalized_manifest_rejects_unsafe_and_invalid_updates(tmp_path: Path) -> None:
    entry, digest, _ = prepare_normalization_fixture(tmp_path)
    document = normalize_document(entry, dataset_dir=tmp_path, expected_sha256=digest)
    outside = tmp_path.parent / "outside-normalized.json"
    write_normalized(document, outside)
    with pytest.raises(ArtifactError, match="inside the dataset"):
        update_normalized_manifest(
            dataset_dir=tmp_path,
            normalized_path=outside,
            document=document,
        )

    wrong_stage = tmp_path / "draft.json"
    write_normalized(document, wrong_stage)
    with pytest.raises(ArtifactError, match="below normalized"):
        update_normalized_manifest(
            dataset_dir=tmp_path,
            normalized_path=wrong_stage,
            document=document,
        )

    normalized_path = tmp_path / "normalized" / f"{entry.filing_id}.json"
    write_normalized(document, normalized_path)
    with pytest.raises(ArtifactError, match="reviewer must not be empty"):
        update_normalized_manifest(
            dataset_dir=tmp_path,
            normalized_path=normalized_path,
            document=document,
            reviewer=" ",
        )
