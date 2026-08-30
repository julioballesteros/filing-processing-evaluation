"""Tests for normalized-document HTML rendering."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from filing_processing_evaluation.artifacts import load_normalized, write_normalized
from filing_processing_evaluation.dataset import DatasetError
from filing_processing_evaluation.models import FormType
from filing_processing_evaluation.rendering import (
    relative_href,
    render_normalized_html,
    write_rendered_html,
)
from tests.support import normalize_document, prepare_normalization_fixture


def test_normalized_round_trip_and_renderer(tmp_path: Path) -> None:
    entry, digest, _ = prepare_normalization_fixture(tmp_path)
    document = normalize_document(entry, dataset_dir=tmp_path, expected_sha256=digest)
    normalized_path = tmp_path / "normalized" / "example.json"
    write_normalized(document, normalized_path)

    loaded = load_normalized(normalized_path)
    raw_href = relative_href(tmp_path / entry.raw_path, tmp_path / "view.html")
    json_href = relative_href(normalized_path, tmp_path / "view.html")
    html = render_normalized_html(loaded, raw_href=raw_href, json_href=json_href)
    rendered_path = tmp_path / "view.html"
    write_rendered_html(html, rendered_path)

    assert load_normalized(normalized_path) == document
    assert isinstance(loaded["document"]["form_type"], FormType)
    assert rendered_path.read_text(encoding="utf-8") == html
    assert "Raw SEC filing" in html
    assert "Open normalized JSON" in html
    assert "Text with normalized whitespace &amp; facts." in html
    assert 'rowspan="2"' in html
    assert "<th " in html
    assert "Source page 1" in html
    assert "hidden XBRL metadata" not in html


def test_renderer_rejects_invalid_shapes() -> None:
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
