"""Tests for the Typer command-line application."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import pytest
from typer.testing import CliRunner

from filing_processing_evaluation.artifacts import load_normalized
from filing_processing_evaluation.cli import app
from filing_processing_evaluation.dataset import DownloadSummary, load_lock
from tests.support import (
    MANIFEST,
    prepare_batch_normalization_fixture,
    prepare_normalization_fixture,
)

CLI_RUNNER = CliRunner()


def test_cli_help_lists_flat_commands() -> None:
    result = CLI_RUNNER.invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in (
        "validate",
        "download",
        "normalize",
        "accept-normalized",
        "render",
    ):
        assert command in result.output


def test_validate_cli_supports_form_filter() -> None:
    result = CLI_RUNNER.invoke(
        app, ["validate", "--manifest", str(MANIFEST), "--form", "10-Q"]
    )

    assert result.exit_code == 0
    assert result.output == "Valid dataset: 10 filings (0 10-K, 10 10-Q, 0 8-K)\n"


def test_download_cli_requires_user_agent() -> None:
    result = CLI_RUNNER.invoke(
        app,
        ["download", "--manifest", str(MANIFEST)],
        env={"SEC_USER_AGENT": ""},
    )

    assert result.exit_code == 2
    assert "SEC_USER_AGENT is required" in result.output


def test_download_cli_delegates_to_downloader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "filing_processing_evaluation.cli.download_filings",
        lambda *args, **kwargs: DownloadSummary(filings=1, artifacts=1),
    )

    result = CLI_RUNNER.invoke(
        app,
        [
            "download",
            "--manifest",
            str(MANIFEST),
            "--filing-id",
            "sec-0001018724-26-000004",
        ],
        env={"SEC_USER_AGENT": "test test@example.com"},
    )

    assert result.exit_code == 0
    assert result.output == "Raw dataset ready: 1 filing(s), 1 artifact(s)\n"


def test_normalization_and_render_cli(tmp_path: Path) -> None:
    entry, _, manifest = prepare_normalization_fixture(tmp_path)
    normalized_path = tmp_path / "normalized.json"
    rendered_path = tmp_path / "rendered.html"

    normalize_result = CLI_RUNNER.invoke(
        app,
        [
            "normalize",
            "--manifest",
            str(manifest),
            "--filing-id",
            entry.filing_id,
            "--output",
            str(normalized_path),
            "--diagnostics",
        ],
    )
    render_result = CLI_RUNNER.invoke(
        app,
        [
            "render",
            "--manifest",
            str(manifest),
            "--input",
            str(normalized_path),
            "--output",
            str(rendered_path),
            "--compare-raw",
        ],
    )

    assert normalize_result.exit_code == 0
    assert render_result.exit_code == 0
    assert normalized_path.is_file()
    assert rendered_path.is_file()
    assert "Normalized draft" in normalize_result.output
    assert "parse_xhtml: xhtml_parsed" in normalize_result.output
    assert "assemble_document: document_validated" in normalize_result.output
    assert "Rendered normalized filing" in render_result.output


def test_normalize_cli_explicitly_rejects_8k() -> None:
    result = CLI_RUNNER.invoke(
        app,
        ["normalize", "--manifest", str(MANIFEST), "--form", "8-K"],
    )

    assert result.exit_code == 2
    assert "8-K raw artifacts are not supported by normalization yet" in result.output


def test_default_normalize_and_accept_cli_update_reference_manifest(
    tmp_path: Path,
) -> None:
    entry, _, manifest = prepare_normalization_fixture(tmp_path)

    normalize_result = CLI_RUNNER.invoke(
        app,
        [
            "normalize",
            "--manifest",
            str(manifest),
            "--filing-id",
            entry.filing_id,
        ],
    )
    assert normalize_result.exit_code == 0
    reference_manifest = tmp_path / "normalized" / "manifest.jsonl"
    assert json.loads(reference_manifest.read_text())["status"] == "draft"

    accept_result = CLI_RUNNER.invoke(
        app,
        [
            "accept-normalized",
            "--manifest",
            str(manifest),
            "--filing-id",
            entry.filing_id,
            "--reviewer",
            "Test Reviewer",
        ],
    )
    assert accept_result.exit_code == 0
    assert json.loads(reference_manifest.read_text())["status"] == "reviewed"
    assert "Normalized manifest" in normalize_result.output
    assert "Accepted normalized reference" in accept_result.output


def test_normalize_cli_processes_manifest_in_batch(tmp_path: Path) -> None:
    entries, manifest = prepare_batch_normalization_fixture(tmp_path)

    result = CLI_RUNNER.invoke(app, ["normalize", "--manifest", str(manifest)])

    assert result.exit_code == 0

    for entry in entries:
        assert (tmp_path / "normalized" / f"{entry.filing_id}.json").is_file()
    reference_entries = [
        json.loads(line)
        for line in (tmp_path / "normalized" / "manifest.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [entry["filing_id"] for entry in reference_entries] == sorted(
        entry.filing_id for entry in entries
    )
    assert "Normalization complete: 2 succeeded, 0 failed" in result.output


def test_normalize_cli_filters_batch_and_rejects_ambiguous_output(
    tmp_path: Path,
) -> None:
    entries, manifest = prepare_batch_normalization_fixture(tmp_path)
    output = tmp_path / "one.json"

    filtered_result = CLI_RUNNER.invoke(
        app,
        [
            "normalize",
            "--manifest",
            str(manifest),
            "--form",
            "10-K",
            "--output",
            str(output),
        ],
    )
    assert filtered_result.exit_code == 0
    assert load_normalized(output)["filing_id"] == entries[0].filing_id

    ambiguous_result = CLI_RUNNER.invoke(
        app,
        [
            "normalize",
            "--manifest",
            str(manifest),
            "--output",
            str(tmp_path / "ambiguous.json"),
        ],
    )
    assert ambiguous_result.exit_code == 2
    assert "--output requires" in ambiguous_result.output


def test_normalize_cli_reports_batch_failures_and_continues(tmp_path: Path) -> None:
    entries, manifest = prepare_batch_normalization_fixture(tmp_path)
    invalid_payload = b"not xml"
    invalid_path = tmp_path / entries[1].raw_path
    invalid_path.write_bytes(invalid_payload)
    locks = load_lock(tmp_path / "raw.lock.jsonl")
    lock_lines = []
    for entry in entries:
        lock = locks[(entry.filing_id, "primary")]
        if entry == entries[1]:
            lock_lines.append(
                {
                    "artifact_id": "primary",
                    "filing_id": entry.filing_id,
                    "retrieved_at": lock.retrieved_at,
                    "sha256": hashlib.sha256(invalid_payload).hexdigest(),
                    "size_bytes": len(invalid_payload),
                }
            )
        else:
            lock_lines.append(asdict(lock))
    (tmp_path / "raw.lock.jsonl").write_text(
        "".join(json.dumps(lock) + "\n" for lock in lock_lines), encoding="utf-8"
    )

    result = CLI_RUNNER.invoke(app, ["normalize", "--manifest", str(manifest)])

    assert result.exit_code == 2
    assert (tmp_path / "normalized" / f"{entries[0].filing_id}.json").is_file()
    assert not (tmp_path / "normalized" / f"{entries[1].filing_id}.json").exists()
    assert f"error: {entries[1].filing_id}" in result.output
    assert "Normalization complete: 1 succeeded, 1 failed" in result.output


def test_normalize_cli_rejects_invalid_batch_selection(tmp_path: Path) -> None:
    entries, manifest = prepare_batch_normalization_fixture(tmp_path)

    unknown_result = CLI_RUNNER.invoke(
        app,
        [
            "normalize",
            "--manifest",
            str(manifest),
            "--filing-id",
            "unknown",
        ],
    )
    assert unknown_result.exit_code == 2
    assert "unknown filing IDs" in unknown_result.output

    empty_result = CLI_RUNNER.invoke(
        app,
        [
            "normalize",
            "--manifest",
            str(manifest),
            "--form",
            "10-Q",
            "--filing-id",
            entries[0].filing_id,
        ],
    )
    assert empty_result.exit_code == 2
    assert "filing selection is empty" in empty_result.output

    locks = load_lock(tmp_path / "raw.lock.jsonl")
    (tmp_path / "raw.lock.jsonl").write_text(
        json.dumps(asdict(locks[(entries[0].filing_id, "primary")])) + "\n",
        encoding="utf-8",
    )
    missing_lock_result = CLI_RUNNER.invoke(
        app, ["normalize", "--manifest", str(manifest)]
    )
    assert missing_lock_result.exit_code == 2
    assert (
        f"no raw lock entry for: {entries[1].filing_id}" in missing_lock_result.output
    )
