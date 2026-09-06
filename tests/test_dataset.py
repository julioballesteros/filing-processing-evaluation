"""Tests for manifest validation and reproducible raw downloads."""

from __future__ import annotations

import io
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import NoReturn

import pytest

from filing_processing_evaluation.dataset import (
    DatasetError,
    download_filings,
    load_lock,
    load_manifest,
    validate_dataset,
)
from filing_processing_evaluation.models import FormType
from tests.support import MANIFEST, write_manifest


def test_committed_manifest_has_ten_companies_and_three_forms() -> None:
    entries = load_manifest(MANIFEST)

    assert len(entries) == 30
    assert all(isinstance(entry.form_type, FormType) for entry in entries)
    assert Counter(entry.form_type for entry in entries) == {
        FormType.TEN_K: 10,
        FormType.TEN_Q: 10,
        FormType.EIGHT_K: 10,
    }
    forms_by_cik: dict[str, set[FormType]] = {}
    for entry in entries:
        forms_by_cik.setdefault(entry.cik, set()).add(entry.form_type)
    assert len(forms_by_cik) == 10
    assert all(
        forms == {FormType.TEN_K, FormType.TEN_Q, FormType.EIGHT_K}
        for forms in forms_by_cik.values()
    )
    earnings_filings = [
        entry for entry in entries if entry.form_type == FormType.EIGHT_K
    ]
    assert all(
        any(exhibit.artifact_id == "earnings-release" for exhibit in entry.exhibits)
        for entry in earnings_filings
    )


def test_download_and_validate_raw_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = load_manifest(MANIFEST)[0]
    manifest = tmp_path / "manifest.jsonl"
    write_manifest(manifest, [entry])
    payload = b"<html><body>filing</body></html>"
    monkeypatch.setattr(
        "filing_processing_evaluation.dataset.urlopen",
        lambda request, timeout: io.BytesIO(payload),
    )

    summary = download_filings(
        [entry], dataset_dir=tmp_path, user_agent="test test@example.com", delay=0
    )

    assert summary.filings == 1
    assert summary.artifacts == 1
    assert (tmp_path / entry.raw_path).read_bytes() == payload
    lock = load_lock(tmp_path / "raw.lock.jsonl")
    assert lock[(entry.filing_id, "primary")].size_bytes == len(payload)
    assert validate_dataset(manifest, check_raw=True).total == 1

    def unexpected_download(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("an intact existing artifact must not be downloaded")

    monkeypatch.setattr(
        "filing_processing_evaluation.dataset.urlopen", unexpected_download
    )
    cached = download_filings(
        [entry], dataset_dir=tmp_path, user_agent="test@example.com", delay=0
    )
    assert cached.filings == 1
    assert cached.artifacts == 1


def test_download_includes_selected_earnings_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = next(
        filing
        for filing in load_manifest(MANIFEST)
        if filing.form_type == FormType.EIGHT_K
    )
    manifest = tmp_path / "manifest.jsonl"
    write_manifest(manifest, [entry])
    payload = b"<html><body>artifact</body></html>"
    monkeypatch.setattr(
        "filing_processing_evaluation.dataset.urlopen",
        lambda request, timeout: io.BytesIO(payload),
    )

    summary = download_filings(
        [entry], dataset_dir=tmp_path, user_agent="test@example.com", delay=0
    )

    assert summary.filings == 1
    assert summary.artifacts == 2
    for artifact in entry.raw_artifacts:
        assert (tmp_path / artifact.raw_path).read_bytes() == payload
    lock = load_lock(tmp_path / "raw.lock.jsonl")
    assert set(lock) == {
        (entry.filing_id, "primary"),
        (entry.filing_id, "earnings-release"),
    }
    assert validate_dataset(manifest, check_raw=True).total == 1


def test_download_rejects_selection_and_integrity_errors(tmp_path: Path) -> None:
    entry = load_manifest(MANIFEST)[0]
    with pytest.raises(DatasetError, match="delay cannot be negative"):
        download_filings(
            [entry], dataset_dir=tmp_path, user_agent="test@example.com", delay=-1
        )
    with pytest.raises(DatasetError, match="unknown filing IDs"):
        download_filings(
            [entry],
            dataset_dir=tmp_path,
            user_agent="test@example.com",
            filing_ids={"unknown"},
            delay=0,
        )

    destination = tmp_path / entry.raw_path
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"changed")
    lock = {
        "artifact_id": "primary",
        "filing_id": entry.filing_id,
        "retrieved_at": "2026-08-27T10:00:00Z",
        "sha256": "0" * 64,
        "size_bytes": 1,
    }
    (tmp_path / "raw.lock.jsonl").write_text(json.dumps(lock) + "\n")
    with pytest.raises(DatasetError, match="failed integrity check"):
        download_filings(
            [entry], dataset_dir=tmp_path, user_agent="test@example.com", delay=0
        )


def test_validate_requires_locked_raw_artifacts(tmp_path: Path) -> None:
    entry = load_manifest(MANIFEST)[0]
    manifest = tmp_path / "manifest.jsonl"
    write_manifest(manifest, [entry])

    with pytest.raises(DatasetError, match="no raw lock entry"):
        validate_dataset(manifest, check_raw=True)


def test_manifest_rejects_bad_json_duplicates_and_fields(tmp_path: Path) -> None:
    path = tmp_path / "manifest.jsonl"
    path.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="invalid JSON"):
        load_manifest(path)

    entry = load_manifest(MANIFEST)[0]
    write_manifest(path, [entry, entry])
    with pytest.raises(DatasetError, match="duplicate filing_id"):
        load_manifest(path)

    invalid = asdict(entry)
    del invalid["ticker"]
    path.write_text(json.dumps(invalid) + "\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="invalid fields"):
        load_manifest(path)


def test_earnings_8k_requires_named_earnings_release_exhibit(
    tmp_path: Path,
) -> None:
    entry = next(
        filing
        for filing in load_manifest(MANIFEST)
        if filing.form_type == FormType.EIGHT_K
    )
    invalid = asdict(entry)
    invalid["exhibits"][0]["artifact_id"] = "press-release"
    path = tmp_path / "manifest.jsonl"
    path.write_text(json.dumps(invalid) + "\n", encoding="utf-8")

    with pytest.raises(DatasetError, match="earnings-release exhibit"):
        load_manifest(path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("form_type", "6-K", "unsupported form_type"),
        ("cik", "123", "CIK must contain"),
        ("accession_number", "bad", "invalid accession"),
        ("filing_date", "yesterday", "invalid filing_date"),
        ("source_url", "https://example.com/file", "SEC HTTPS archive"),
        ("raw_path", "../file.htm", "safe path"),
        ("raw_path", "raw/wrong.htm", "raw/<filing_id>"),
    ],
)
def test_manifest_rejects_invalid_values(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    entry = load_manifest(MANIFEST)[0]
    invalid = asdict(entry)
    invalid[field] = value
    if field == "accession_number":
        invalid["filing_id"] = "sec-bad"
    path = tmp_path / "manifest.jsonl"
    path.write_text(json.dumps(invalid) + "\n", encoding="utf-8")

    with pytest.raises(DatasetError, match=message):
        load_manifest(path)


def test_lock_rejects_invalid_and_unknown_entries(tmp_path: Path) -> None:
    lock_path = tmp_path / "raw.lock.jsonl"
    invalid = {
        "artifact_id": "primary",
        "filing_id": "sec-id",
        "sha256": "bad",
        "size_bytes": 0,
        "retrieved_at": "never",
    }
    lock_path.write_text(json.dumps(invalid) + "\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="invalid SHA-256"):
        load_lock(lock_path)

    entry = load_manifest(MANIFEST)[0]
    manifest = tmp_path / "manifest.jsonl"
    write_manifest(manifest, [entry])
    unknown = {
        "artifact_id": "primary",
        "filing_id": "sec-0000000000-00-000000",
        "sha256": "0" * 64,
        "size_bytes": 1,
        "retrieved_at": "2026-08-27T10:00:00Z",
    }
    lock_path.write_text(json.dumps(unknown) + "\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="unknown artifacts"):
        validate_dataset(manifest)
