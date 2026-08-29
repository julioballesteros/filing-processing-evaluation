"""Tests for the committed dataset and command-line interface."""

from __future__ import annotations

import hashlib
import io
import json
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any, NoReturn

import pytest
from typer.testing import CliRunner

from filing_processing_evaluation.cli import app
from filing_processing_evaluation.dataset import (
    DatasetError,
    Filing,
    download_filings,
    load_lock,
    load_manifest,
    validate_dataset,
)
from filing_processing_evaluation.normalization import (
    NormalizationService,
    _normalize_table,
    _validate_table,
    load_normalized,
    normalize_filing,
    normalize_text,
    update_normalized_manifest,
    validate_normalized,
    write_normalized,
)
from filing_processing_evaluation.rendering import (
    relative_href,
    render_normalized_html,
    write_rendered_html,
)

ROOT = Path(__file__).parents[1]
MANIFEST = ROOT / "dataset" / "manifest.jsonl"
CLI_RUNNER = CliRunner()
SAMPLE_XHTML = b"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en-US">
  <head><title>Example Filing</title></head>
  <body>
    <div style="display:none"><span>hidden XBRL metadata</span></div>
    <table><tr><td></td></tr></table>
    <div><span style="font-weight:700">PART I - FINANCIAL INFORMATION</span></div>
    <div style="-sec-extract:summary"><span style="font-weight:700">Item 1. Statements</span></div>
    <div><span>Text&#160;with   <em>normalized</em> whitespace &amp; <span>facts</span>.</span></div>
    <div><span>(2)</span><span>In May</span> <span>Greater</span><span>China</span></div>
    <div><span style="font-style:italic;font-weight:400">Subheading</span></div>
    <div style="text-align:center"><span style="font-weight:700">(408) 996-1010</span></div>
    <ul><li>First item</li></ul>
    <table>
      <caption>Summary &amp; values</caption>
      <tr><th rowspan="2">Label</th><td>2026</td></tr>
      <tr><td colspan="0">42</td></tr>
    </table>
    <div><span>Example Inc. | Q1 2026 Form 10-Q | 1</span></div>
    <hr/>
  </body>
</html>
"""


def _write_manifest(path: Path, entries: list[Filing]) -> None:
    path.write_text(
        "".join(json.dumps(asdict(entry), sort_keys=True) + "\n" for entry in entries),
        encoding="utf-8",
    )


def _prepare_normalization_fixture(tmp_path: Path) -> tuple[Filing, str, Path]:
    entry = load_manifest(MANIFEST)[0]
    raw_path = tmp_path / entry.raw_path
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(SAMPLE_XHTML)
    digest = hashlib.sha256(SAMPLE_XHTML).hexdigest()
    manifest = tmp_path / "manifest.jsonl"
    _write_manifest(manifest, [entry])
    lock = {
        "filing_id": entry.filing_id,
        "retrieved_at": "2026-08-27T10:00:00Z",
        "sha256": digest,
        "size_bytes": len(SAMPLE_XHTML),
    }
    (tmp_path / "raw.lock.jsonl").write_text(json.dumps(lock) + "\n")
    return entry, digest, manifest


def _prepare_batch_normalization_fixture(
    tmp_path: Path,
) -> tuple[list[Filing], Path]:
    entries = load_manifest(MANIFEST)[:2]
    manifest = tmp_path / "manifest.jsonl"
    _write_manifest(manifest, entries)
    locks = []
    for entry in entries:
        raw_path = tmp_path / entry.raw_path
        raw_path.parent.mkdir(parents=True)
        raw_path.write_bytes(SAMPLE_XHTML)
        locks.append(
            {
                "filing_id": entry.filing_id,
                "retrieved_at": "2026-08-27T10:00:00Z",
                "sha256": hashlib.sha256(SAMPLE_XHTML).hexdigest(),
                "size_bytes": len(SAMPLE_XHTML),
            }
        )
    (tmp_path / "raw.lock.jsonl").write_text(
        "".join(json.dumps(lock) + "\n" for lock in locks), encoding="utf-8"
    )
    return entries, manifest


def test_committed_manifest_has_ten_paired_companies_and_forms() -> None:
    entries = load_manifest(MANIFEST)

    assert len(entries) == 20
    assert Counter(entry.form_type for entry in entries) == {"10-K": 10, "10-Q": 10}
    forms_by_cik: dict[str, set[str]] = {}
    for entry in entries:
        forms_by_cik.setdefault(entry.cik, set()).add(entry.form_type)
    assert len(forms_by_cik) == 10
    assert all(forms == {"10-K", "10-Q"} for forms in forms_by_cik.values())


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
    assert result.output == "Valid dataset: 10 filings (0 10-K, 10 10-Q)\n"


def test_download_cli_requires_user_agent() -> None:
    result = CLI_RUNNER.invoke(
        app,
        ["download", "--manifest", str(MANIFEST)],
        env={"SEC_USER_AGENT": ""},
    )

    assert result.exit_code == 2
    assert "SEC_USER_AGENT is required" in result.output


def test_download_and_validate_raw_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = load_manifest(MANIFEST)[0]
    manifest = tmp_path / "manifest.jsonl"
    _write_manifest(manifest, [entry])
    payload = b"<html><body>filing</body></html>"
    monkeypatch.setattr(
        "filing_processing_evaluation.dataset.urlopen",
        lambda request, timeout: io.BytesIO(payload),
    )

    count = download_filings(
        [entry], dataset_dir=tmp_path, user_agent="test test@example.com", delay=0
    )

    assert count == 1
    assert (tmp_path / entry.raw_path).read_bytes() == payload
    lock = load_lock(tmp_path / "raw.lock.jsonl")
    assert lock[entry.filing_id].size_bytes == len(payload)
    assert validate_dataset(manifest, check_raw=True).total == 1

    def unexpected_download(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("an intact existing artifact must not be downloaded")

    monkeypatch.setattr(
        "filing_processing_evaluation.dataset.urlopen", unexpected_download
    )
    assert (
        download_filings(
            [entry], dataset_dir=tmp_path, user_agent="test@example.com", delay=0
        )
        == 1
    )


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
    _write_manifest(manifest, [entry])

    with pytest.raises(DatasetError, match="no raw lock entry"):
        validate_dataset(manifest, check_raw=True)


def test_manifest_rejects_bad_json_duplicates_and_fields(tmp_path: Path) -> None:
    path = tmp_path / "manifest.jsonl"
    path.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="invalid JSON"):
        load_manifest(path)

    entry = load_manifest(MANIFEST)[0]
    _write_manifest(path, [entry, entry])
    with pytest.raises(DatasetError, match="duplicate filing_id"):
        load_manifest(path)

    invalid = asdict(entry)
    del invalid["ticker"]
    path.write_text(json.dumps(invalid) + "\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="invalid fields"):
        load_manifest(path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("form_type", "8-K", "unsupported form_type"),
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
    _write_manifest(manifest, [entry])
    unknown = {
        "filing_id": "sec-0000000000-00-000000",
        "sha256": "0" * 64,
        "size_bytes": 1,
        "retrieved_at": "2026-08-27T10:00:00Z",
    }
    lock_path.write_text(json.dumps(unknown) + "\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="unknown filing IDs"):
        validate_dataset(manifest)


def test_download_cli_delegates_to_downloader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "filing_processing_evaluation.cli.download_filings",
        lambda *args, **kwargs: 1,
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
    assert result.output == "Raw dataset ready: 1 filing(s)\n"


def test_normalize_filing_preserves_structure_and_omits_furniture(
    tmp_path: Path,
) -> None:
    entry, digest, _ = _prepare_normalization_fixture(tmp_path)

    document = NormalizationService().normalize(
        entry, dataset_dir=tmp_path, expected_sha256=digest
    )

    assert document["schema_version"] == "1.1.0"
    assert document["raw_sha256"] == digest
    assert document["document"] == {
        "title": (
            f"{entry.company_name} {entry.form_type} for period ended "
            f"{entry.period_end_date}"
        ),
        "source_title": "Example Filing",
        "language": "en-US",
        "company_name": entry.company_name,
        "form_type": entry.form_type,
        "filing_date": entry.filing_date,
        "period_end_date": entry.period_end_date,
    }
    assert document["page_count"] == 2
    assert document["sections"] == [
        {
            "id": "s0001",
            "order": 1,
            "parent_id": None,
            "level": 1,
            "label": "PART I",
            "title": "FINANCIAL INFORMATION",
            "heading_block_id": "b0001",
        },
        {
            "id": "s0002",
            "order": 2,
            "parent_id": "s0001",
            "level": 2,
            "label": "Item 1",
            "title": "Statements",
            "heading_block_id": "b0002",
        },
    ]
    blocks = document["blocks"]
    assert [block["type"] for block in blocks] == [
        "heading",
        "heading",
        "paragraph",
        "paragraph",
        "heading",
        "paragraph",
        "list_item",
        "table",
    ]
    assert blocks[2]["text"] == "Text with normalized whitespace & facts."
    assert blocks[3]["text"] == "(2) In May Greater China"
    assert blocks[4]["text"] == "Subheading"
    assert blocks[4]["level"] == 3
    assert blocks[5]["text"] == "(408) 996-1010"
    assert blocks[5]["type"] == "paragraph"
    assert all(block["source"]["page_number"] == 1 for block in blocks)
    assert all(block["source"]["path"].startswith("/html/body[1]/") for block in blocks)
    assert all("hidden" not in str(block) for block in blocks)
    table = blocks[7]
    assert table["caption"] == "Summary & values"
    assert (table["row_count"], table["column_count"]) == (2, 2)
    assert table["source_shape"] == {"rows": 2, "columns": 2}
    assert table["cells"][0] == {
        "row": 0,
        "column": 0,
        "row_span": 2,
        "column_span": 1,
        "text": "Label",
        "source_cells": [{"row": 0, "column": 0, "row_span": 2, "column_span": 1}],
        "role": "header",
    }
    assert table["cells"][2]["column"] == 1
    assert table["cells"][2]["column_span"] == 1


def test_normalized_round_trip_and_renderer(tmp_path: Path) -> None:
    entry, digest, _ = _prepare_normalization_fixture(tmp_path)
    document = normalize_filing(entry, dataset_dir=tmp_path, expected_sha256=digest)
    normalized_path = tmp_path / "normalized" / "example.json"
    write_normalized(document, normalized_path)

    loaded = load_normalized(normalized_path)
    raw_href = relative_href(tmp_path / entry.raw_path, tmp_path / "view.html")
    json_href = relative_href(normalized_path, tmp_path / "view.html")
    html = render_normalized_html(loaded, raw_href=raw_href, json_href=json_href)
    rendered_path = tmp_path / "view.html"
    write_rendered_html(html, rendered_path)

    assert load_normalized(normalized_path) == document
    assert rendered_path.read_text(encoding="utf-8") == html
    assert "Raw SEC filing" in html
    assert "Open normalized JSON" in html
    assert "Text with normalized whitespace &amp; facts." in html
    assert 'rowspan="2"' in html
    assert "<th " in html
    assert "Source page 1" in html
    assert "hidden XBRL metadata" not in html


def _normalized_table(source: str) -> dict[str, Any]:
    table = _normalize_table(ET.fromstring(source))
    assert table is not None
    table["id"] = "test-table"
    _validate_table(table)
    return table


def test_logical_table_aligns_offset_currency_fragments_to_numeric_columns() -> None:
    table = _normalized_table("""<table>
        <tr><td>Year ended</td><td colspan="2"></td><td>2024</td><td colspan="3"></td><td>2023</td><td></td></tr>
        <tr><td>Revenue</td><td></td><td>$</td><td>247,442</td><td colspan="2"></td><td>$</td><td>219,790</td><td></td></tr>
        <tr><td>Net income</td><td colspan="2"></td><td>88,308</td><td colspan="3"></td><td>71,383</td><td></td></tr>
        </table>""")

    revenue = [cell for cell in table["cells"] if cell["row"] == 1]
    assert [(cell["column"], cell["text"]) for cell in revenue] == [
        (0, "Revenue"),
        (1, "$247,442"),
        (2, "$219,790"),
    ]


def test_logical_table_keeps_wide_and_row_spanning_headers_disjoint() -> None:
    wide_header = _normalized_table("""<table>
        <tr><td rowspan="2" colspan="3">(in millions)</td><td colspan="9"></td><td colspan="15">Year ended December 31,</td><td colspan="6"></td></tr>
        <tr><td colspan="15"></td><td colspan="3">2025</td><td colspan="3"></td><td colspan="3">2024</td><td colspan="3"></td><td colspan="3">2023</td></tr>
        <tr><td colspan="3">Operating activities</td><td colspan="15"></td><td>$</td><td>(147)</td><td colspan="4"></td><td>$</td><td>(42)</td><td colspan="4"></td><td>$</td><td>13</td><td></td></tr>
        </table>""")
    cells = {cell["text"]: cell for cell in wide_header["cells"]}
    assert (cells["(in millions)"]["column"], cells["(in millions)"]["row_span"]) == (
        0,
        2,
    )
    assert cells["Year ended December 31,"]["column"] == 1
    assert [cells[str(year)]["column"] for year in (2025, 2024, 2023)] == [1, 2, 3]

    tied_header = _normalized_table("""<table>
        <tr><td colspan="33"></td><td rowspan="2" colspan="3">Collateral</td><td colspan="3"></td></tr>
        <tr><td colspan="30"></td><td colspan="3">Nonperforming</td></tr>
        <tr><td colspan="3">Exposure</td><td colspan="27"></td><td>10</td><td colspan="5"></td><td>20</td><td colspan="2"></td></tr>
        <tr><td colspan="3">Other</td><td colspan="27"></td><td>11</td><td colspan="5"></td><td>21</td><td colspan="2"></td></tr>
        </table>""")
    cells = {cell["text"]: cell for cell in tied_header["cells"]}
    assert (cells["Collateral"]["column"], cells["Collateral"]["row_span"]) == (
        2,
        2,
    )
    assert cells["Nonperforming"]["column"] == 1


def test_normalization_and_render_cli(tmp_path: Path) -> None:
    entry, _, manifest = _prepare_normalization_fixture(tmp_path)
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
    assert "Rendered normalized filing" in render_result.output


def test_default_normalize_and_accept_cli_update_reference_manifest(
    tmp_path: Path,
) -> None:
    entry, _, manifest = _prepare_normalization_fixture(tmp_path)

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
    entries, manifest = _prepare_batch_normalization_fixture(tmp_path)

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
    entries, manifest = _prepare_batch_normalization_fixture(tmp_path)
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
    entries, manifest = _prepare_batch_normalization_fixture(tmp_path)
    invalid_payload = b"not xml"
    invalid_path = tmp_path / entries[1].raw_path
    invalid_path.write_bytes(invalid_payload)
    locks = load_lock(tmp_path / "raw.lock.jsonl")
    lock_lines = []
    for entry in entries:
        lock = locks[entry.filing_id]
        if entry == entries[1]:
            lock_lines.append(
                {
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
    entries, manifest = _prepare_batch_normalization_fixture(tmp_path)

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
        json.dumps(asdict(locks[entries[0].filing_id])) + "\n", encoding="utf-8"
    )
    missing_lock_result = CLI_RUNNER.invoke(
        app, ["normalize", "--manifest", str(manifest)]
    )
    assert missing_lock_result.exit_code == 2
    assert (
        f"no raw lock entry for: {entries[1].filing_id}" in missing_lock_result.output
    )


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"not xml", "not valid XHTML"),
        (b'<html xmlns="http://www.w3.org/1999/xhtml"/>', "does not contain"),
        (
            b'<html xmlns="http://www.w3.org/1999/xhtml"><body/></html>',
            "produced no blocks",
        ),
    ],
)
def test_normalizer_rejects_invalid_raw_documents(
    tmp_path: Path, content: bytes, message: str
) -> None:
    entry = load_manifest(MANIFEST)[0]
    raw_path = tmp_path / entry.raw_path
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()

    with pytest.raises(DatasetError, match=message):
        normalize_filing(entry, dataset_dir=tmp_path, expected_sha256=digest)


def test_normalizer_rejects_missing_or_modified_raw_artifact(tmp_path: Path) -> None:
    entry = load_manifest(MANIFEST)[0]
    with pytest.raises(DatasetError, match="raw artifact not found"):
        normalize_filing(entry, dataset_dir=tmp_path, expected_sha256="0" * 64)

    raw_path = tmp_path / entry.raw_path
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(SAMPLE_XHTML)
    with pytest.raises(DatasetError, match="failed integrity check"):
        normalize_filing(entry, dataset_dir=tmp_path, expected_sha256="0" * 64)


def test_normalized_loader_and_renderer_reject_invalid_shapes(tmp_path: Path) -> None:
    path = tmp_path / "document.json"
    path.write_text("not json")
    with pytest.raises(DatasetError, match="invalid JSON"):
        load_normalized(path)
    path.write_text("[]")
    with pytest.raises(DatasetError, match="JSON object"):
        load_normalized(path)
    path.write_text(json.dumps({"schema_version": "0.0.0"}))
    with pytest.raises(DatasetError, match="unsupported schema"):
        load_normalized(path)

    malformed: dict[str, Any] = {
        "filing_id": "id",
        "raw_sha256": "0" * 64,
        "document": "bad",
        "sections": [],
        "blocks": [],
    }
    with pytest.raises(DatasetError, match="metadata must be an object"):
        render_normalized_html(malformed)
    malformed["document"] = {}
    malformed["blocks"] = "bad"
    with pytest.raises(DatasetError, match="must be arrays"):
        render_normalized_html(malformed)


def test_normalize_text_uses_unicode_nfc() -> None:
    assert normalize_text(" Cafe\u0301\u00a0  report ") == "Café report"


def test_normalized_manifest_tracks_hash_and_review_state(tmp_path: Path) -> None:
    entry, digest, _ = _prepare_normalization_fixture(tmp_path)
    document = normalize_filing(entry, dataset_dir=tmp_path, expected_sha256=digest)
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
    entry, digest, _ = _prepare_normalization_fixture(tmp_path)
    document = normalize_filing(entry, dataset_dir=tmp_path, expected_sha256=digest)
    outside = tmp_path.parent / "outside-normalized.json"
    write_normalized(document, outside)
    with pytest.raises(DatasetError, match="inside the dataset"):
        update_normalized_manifest(
            dataset_dir=tmp_path,
            normalized_path=outside,
            document=document,
        )

    wrong_stage = tmp_path / "draft.json"
    write_normalized(document, wrong_stage)
    with pytest.raises(DatasetError, match="below normalized"):
        update_normalized_manifest(
            dataset_dir=tmp_path,
            normalized_path=wrong_stage,
            document=document,
        )

    normalized_path = tmp_path / "normalized" / f"{entry.filing_id}.json"
    write_normalized(document, normalized_path)
    with pytest.raises(DatasetError, match="reviewer must not be empty"):
        update_normalized_manifest(
            dataset_dir=tmp_path,
            normalized_path=normalized_path,
            document=document,
            reviewer=" ",
        )


def test_semantic_validator_rejects_broken_references_and_tables(
    tmp_path: Path,
) -> None:
    entry, digest, _ = _prepare_normalization_fixture(tmp_path)
    document = normalize_filing(entry, dataset_dir=tmp_path, expected_sha256=digest)

    duplicate = json.loads(json.dumps(document))
    duplicate["blocks"][1]["id"] = duplicate["blocks"][0]["id"]
    with pytest.raises(DatasetError, match="IDs must be unique"):
        validate_normalized(duplicate)

    invalid_page = json.loads(json.dumps(document))
    invalid_page["blocks"][0]["source"]["page_number"] = 0
    with pytest.raises(DatasetError, match="invalid provenance"):
        validate_normalized(invalid_page)

    overlapping = json.loads(json.dumps(document))
    table = next(block for block in overlapping["blocks"] if block["type"] == "table")
    table["cells"][1]["column"] = table["cells"][0]["column"]
    with pytest.raises(DatasetError, match="cells overlap"):
        validate_normalized(overlapping)


def test_semantic_validator_rejects_invalid_metadata_and_cell_provenance(
    tmp_path: Path,
) -> None:
    entry, digest, _ = _prepare_normalization_fixture(tmp_path)
    document = normalize_filing(entry, dataset_dir=tmp_path, expected_sha256=digest)

    bad_metadata = json.loads(json.dumps(document))
    bad_metadata["document"].pop("company_name")
    with pytest.raises(DatasetError, match="metadata has invalid fields"):
        validate_normalized(bad_metadata)

    bad_form = json.loads(json.dumps(document))
    bad_form["document"]["form_type"] = "8-K"
    with pytest.raises(DatasetError, match="unsupported form type"):
        validate_normalized(bad_form)

    bad_hash = json.loads(json.dumps(document))
    bad_hash["raw_sha256"] = "bad"
    with pytest.raises(DatasetError, match="invalid raw SHA"):
        validate_normalized(bad_hash)

    bad_type = json.loads(json.dumps(document))
    bad_type["blocks"][0]["type"] = "unknown"
    with pytest.raises(DatasetError, match="invalid type"):
        validate_normalized(bad_type)

    bad_table = json.loads(json.dumps(document))
    table = next(block for block in bad_table["blocks"] if block["type"] == "table")
    table["source_shape"] = {}
    with pytest.raises(DatasetError, match="invalid source shape"):
        validate_normalized(bad_table)

    bad_role = json.loads(json.dumps(document))
    table = next(block for block in bad_role["blocks"] if block["type"] == "table")
    table["cells"][0]["role"] = "unknown"
    with pytest.raises(DatasetError, match="invalid cell role"):
        validate_normalized(bad_role)

    missing_source = json.loads(json.dumps(document))
    table = next(
        block for block in missing_source["blocks"] if block["type"] == "table"
    )
    table["cells"][0]["source_cells"] = []
    with pytest.raises(DatasetError, match="no source coordinates"):
        validate_normalized(missing_source)
