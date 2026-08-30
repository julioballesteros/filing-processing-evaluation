"""Tests for normalized-document semantic validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from filing_processing_evaluation.normalization import (
    NormalizationError,
    validate_normalized,
)
from tests.support import normalize_document, prepare_normalization_fixture


def test_semantic_validator_rejects_broken_references_and_tables(
    tmp_path: Path,
) -> None:
    entry, digest, _ = prepare_normalization_fixture(tmp_path)
    document = normalize_document(entry, dataset_dir=tmp_path, expected_sha256=digest)

    duplicate = json.loads(json.dumps(document))
    duplicate["blocks"][1]["id"] = duplicate["blocks"][0]["id"]
    with pytest.raises(NormalizationError, match="IDs must be unique"):
        validate_normalized(duplicate)

    invalid_page = json.loads(json.dumps(document))
    invalid_page["blocks"][0]["source"]["page_number"] = 0
    with pytest.raises(NormalizationError, match="invalid provenance"):
        validate_normalized(invalid_page)

    overlapping = json.loads(json.dumps(document))
    table = next(block for block in overlapping["blocks"] if block["type"] == "table")
    table["cells"][1]["column"] = table["cells"][0]["column"]
    with pytest.raises(NormalizationError, match="cells overlap"):
        validate_normalized(overlapping)


def test_semantic_validator_rejects_invalid_metadata_and_cell_provenance(
    tmp_path: Path,
) -> None:
    entry, digest, _ = prepare_normalization_fixture(tmp_path)
    document = normalize_document(entry, dataset_dir=tmp_path, expected_sha256=digest)

    bad_metadata = json.loads(json.dumps(document))
    bad_metadata["document"].pop("company_name")
    with pytest.raises(NormalizationError, match="metadata has invalid fields"):
        validate_normalized(bad_metadata)

    bad_form = json.loads(json.dumps(document))
    bad_form["document"]["form_type"] = "8-K"
    with pytest.raises(NormalizationError, match="unsupported form type"):
        validate_normalized(bad_form)

    bad_hash = json.loads(json.dumps(document))
    bad_hash["raw_sha256"] = "bad"
    with pytest.raises(NormalizationError, match="invalid raw SHA"):
        validate_normalized(bad_hash)

    bad_type = json.loads(json.dumps(document))
    bad_type["blocks"][0]["type"] = "unknown"
    with pytest.raises(NormalizationError, match="invalid type"):
        validate_normalized(bad_type)

    bad_table = json.loads(json.dumps(document))
    table = next(block for block in bad_table["blocks"] if block["type"] == "table")
    table["source_shape"] = {}
    with pytest.raises(NormalizationError, match="invalid source shape"):
        validate_normalized(bad_table)

    bad_role = json.loads(json.dumps(document))
    table = next(block for block in bad_role["blocks"] if block["type"] == "table")
    table["cells"][0]["role"] = "unknown"
    with pytest.raises(NormalizationError, match="invalid cell role"):
        validate_normalized(bad_role)

    missing_source = json.loads(json.dumps(document))
    table = next(
        block for block in missing_source["blocks"] if block["type"] == "table"
    )
    table["cells"][0]["source_cells"] = []
    with pytest.raises(NormalizationError, match="no source coordinates"):
        validate_normalized(missing_source)
