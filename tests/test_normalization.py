"""Tests for normalization workflows and service orchestration."""

from __future__ import annotations

import hashlib
import inspect
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from filing_processing_evaluation.artifacts import (
    ArtifactError,
    FileSystemRawFilingLoader,
)
from filing_processing_evaluation.dataset import load_manifest
from filing_processing_evaluation.models import FormType
from filing_processing_evaluation.normalization import (
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
    prepare_normalization_fixture,
)


def test_normalize_filing_preserves_structure_and_omits_furniture(
    tmp_path: Path,
) -> None:
    entry, digest, _ = prepare_normalization_fixture(tmp_path)

    source = FileSystemRawFilingLoader(tmp_path).load(entry, expected_sha256=digest)
    result = NormalizationService().normalize(source)
    document = result.document

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

    entries, _ = prepare_batch_normalization_fixture(tmp_path)
    loader = FileSystemRawFilingLoader(tmp_path)
    digest = hashlib.sha256(SAMPLE_XHTML).hexdigest()
    annual_source = loader.load(entries[0], expected_sha256=digest)
    quarterly_source = loader.load(entries[1], expected_sha256=digest)

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
            form_type=cast(FormType, "8-K"),
        ),
    )
    with pytest.raises(NormalizationError, match="no normalization workflow"):
        NormalizationService().normalize(unsupported_source)
    with pytest.raises(ValueError, match="form types must be unique"):
        NormalizationService((TenKNormalizer(), TenKNormalizer()))


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


def test_normalize_text_uses_unicode_nfc() -> None:
    assert normalize_text(" Cafe\u0301\u00a0  report ") == "Café report"
