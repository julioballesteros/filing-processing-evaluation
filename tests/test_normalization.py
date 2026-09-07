"""Tests for normalization workflows and service orchestration."""

from __future__ import annotations

import hashlib
import inspect
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from filing_processing_evaluation.application import FilingNormalizationApplication
from filing_processing_evaluation.artifacts import (
    ArtifactError,
    FileSystemRawFilingLoader,
)
from filing_processing_evaluation.dataset import load_manifest
from filing_processing_evaluation.models import FormType
from filing_processing_evaluation.normalization import (
    SCHEMA_VERSION,
    EightKNormalizer,
    NormalizationError,
    NormalizationService,
    TenKNormalizer,
    TenQNormalizer,
    normalize_text,
)
from tests.support import (
    MANIFEST,
    SAMPLE_XHTML,
    normalize_document,
    prepare_batch_normalization_fixture,
    prepare_eight_k_normalization_fixture,
    prepare_normalization_fixture,
)


def test_normalize_filing_preserves_structure_and_omits_furniture(
    tmp_path: Path,
) -> None:
    entry, digest, _ = prepare_normalization_fixture(tmp_path)

    source = FileSystemRawFilingLoader(tmp_path).load(
        entry,
        expected_sha256_by_artifact={"primary": digest},
    )
    result = NormalizationService().normalize(source)
    document = result.document

    assert document["schema_version"] == SCHEMA_VERSION
    assert document["source_artifact"] == {
        "artifact_id": "primary",
        "filename": entry.primary_document,
        "document_type": entry.form_type,
        "sha256": digest,
    }
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
        "event_date": None,
        "items": [],
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
    assert [diagnostic.stage for diagnostic in result.diagnostics] == [
        "parse_xhtml",
        "project_visible_html",
        "classify_elements",
        "build_blocks",
        "assemble_document",
    ]


def test_filing_type_workflows_are_separate_and_dispatchable(tmp_path: Path) -> None:
    assert not inspect.signature(TenKNormalizer).parameters
    assert not inspect.signature(TenQNormalizer).parameters
    assert not inspect.signature(EightKNormalizer).parameters

    entries, _ = prepare_batch_normalization_fixture(tmp_path)
    loader = FileSystemRawFilingLoader(tmp_path)
    digest = hashlib.sha256(SAMPLE_XHTML).hexdigest()
    annual_source = loader.load(
        entries[0], expected_sha256_by_artifact={"primary": digest}
    )
    quarterly_source = loader.load(
        entries[1], expected_sha256_by_artifact={"primary": digest}
    )

    annual = TenKNormalizer().normalize(annual_source)
    quarterly = TenQNormalizer().normalize(quarterly_source)

    assert annual.document["document"]["form_type"] == FormType.TEN_K
    assert quarterly.document["document"]["form_type"] == FormType.TEN_Q
    assert NormalizationService().normalize(annual_source) == annual
    assert NormalizationService().normalize(quarterly_source) == quarterly
    with pytest.raises(NormalizationError, match="10-Q workflow cannot normalize"):
        TenQNormalizer().normalize(annual_source)
    unsupported_source = replace(
        annual_source,
        metadata=replace(
            annual_source.metadata,
            form_type=cast(FormType, "6-K"),
        ),
    )
    with pytest.raises(NormalizationError, match="no normalization workflow"):
        NormalizationService().normalize(unsupported_source)
    with pytest.raises(ValueError, match="form types must be unique"):
        NormalizationService((TenKNormalizer(), TenKNormalizer()))


def test_eight_k_workflow_normalizes_earnings_release_exhibit(
    tmp_path: Path,
) -> None:
    entry, hashes, _ = prepare_eight_k_normalization_fixture(tmp_path)
    source = FileSystemRawFilingLoader(tmp_path).load(
        entry,
        expected_sha256_by_artifact=hashes,
    )

    result = NormalizationService().normalize(source)
    document = result.document

    assert document["source_artifact"] == {
        "artifact_id": "earnings-release",
        "filename": entry.exhibits[0].filename,
        "document_type": "EX-99.1",
        "sha256": hashes["earnings-release"],
    }
    assert document["document"]["form_type"] == FormType.EIGHT_K
    assert document["document"]["event_date"] == entry.event_date
    assert document["document"]["items"] == list(entry.items)
    assert document["document"]["source_title"] == "Example Earnings Release"
    assert [section["label"] for section in document["sections"]] == [
        "HIGHLIGHTS",
        "OUTLOOK",
    ]
    assert sum(block["type"] == "table" for block in document["blocks"]) == 1
    assert any(
        block["type"] == "heading" and block.get("text") == "Guidance"
        for block in document["blocks"]
    )
    assert any(
        block["type"] == "paragraph"
        and block.get("text") == "Revenue is expected to increase."
        for block in document["blocks"]
    )
    assert any(
        block.get("text") == "Revenue increased & operating income improved."
        for block in document["blocks"]
    )
    assert result.diagnostics[0].stage == "parse_sec_html"
    projection_diagnostic = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.stage == "project_visible_html"
    )
    assert projection_diagnostic.details["image_elements"] == 1
    block_diagnostic = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.stage == "build_blocks"
    )
    assert block_diagnostic.details["flattened_layout_tables"] == 1
    assert block_diagnostic.details["flattened_layout_blocks"] == 2


def test_repeated_page_section_headings_reuse_active_sections(tmp_path: Path) -> None:
    entry = load_manifest(MANIFEST)[0]
    content = b"""<html xmlns="http://www.w3.org/1999/xhtml">
      <head><title>Continuation headings</title></head>
      <body>
        <div>PART I</div>
        <div>Item 1</div>
        <div><span style="white-space:pre-wrap;min-width:fit-content">ITEM 1. FINA</span><span style="white-space:pre-wrap;min-width:fit-content">NCIAL STATEMENTS</span></div>
        <div>First-page content.</div>
        <div>2026</div>
        <div>1</div>
        <hr/>
        <div>PART I</div>
        <div>Item 1</div>
        <div>Second-page content.</div>
        <div>2</div>
        <hr/>
        <div>PART II</div>
        <div>Item 1</div>
        <div>Different-part content.</div>
        <div>3</div>
      </body>
    </html>"""
    raw_path = tmp_path / entry.raw_path
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(content)

    result = FilingNormalizationApplication(
        FileSystemRawFilingLoader(tmp_path)
    ).normalize(
        entry,
        expected_sha256_by_artifact={"primary": hashlib.sha256(content).hexdigest()},
    )

    assert [
        (section["label"], section["title"]) for section in result.document["sections"]
    ] == [
        ("PART I", None),
        ("Item 1", "FINANCIAL STATEMENTS"),
        ("PART II", None),
        ("Item 1", None),
    ]
    item_section = result.document["sections"][1]
    content_blocks = [
        block
        for block in result.document["blocks"]
        if block.get("text") in {"First-page content.", "Second-page content."}
    ]
    assert {block["section_id"] for block in content_blocks} == {item_section["id"]}
    different_part_block = next(
        block
        for block in result.document["blocks"]
        if block.get("text") == "Different-part content."
    )
    assert different_part_block["section_id"] == result.document["sections"][3]["id"]
    assert any(block.get("text") == "2026" for block in result.document["blocks"])
    assert not any(
        block.get("text") in {"1", "2", "3"} for block in result.document["blocks"]
    )
    assert (
        next(
            diagnostic.details["discarded_page_numbers"]
            for diagnostic in result.diagnostics
            if diagnostic.stage == "classify_elements"
        )
        == 3
    )
    assert (
        next(
            diagnostic.details["repeated_section_headings"]
            for diagnostic in result.diagnostics
            if diagnostic.stage == "build_blocks"
        )
        == 3
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

    with pytest.raises(NormalizationError, match=message):
        normalize_document(entry, dataset_dir=tmp_path, expected_sha256=digest)


def test_normalizer_rejects_missing_or_modified_raw_artifact(tmp_path: Path) -> None:
    entry = load_manifest(MANIFEST)[0]
    with pytest.raises(ArtifactError, match="raw artifact not found"):
        normalize_document(entry, dataset_dir=tmp_path, expected_sha256="0" * 64)

    raw_path = tmp_path / entry.raw_path
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(SAMPLE_XHTML)
    with pytest.raises(ArtifactError, match="failed integrity check"):
        normalize_document(entry, dataset_dir=tmp_path, expected_sha256="0" * 64)

    eight_k, hashes, _ = prepare_eight_k_normalization_fixture(tmp_path)
    with pytest.raises(ArtifactError, match="hashes do not match"):
        FileSystemRawFilingLoader(tmp_path).load(
            eight_k,
            expected_sha256_by_artifact={"primary": hashes["primary"]},
        )


def test_normalize_text_uses_unicode_nfc() -> None:
    assert normalize_text(" Cafe\u0301\u00a0  report ") == "Café report"
