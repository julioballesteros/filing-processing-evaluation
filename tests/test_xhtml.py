"""Tests for XHTML text reconstruction."""

import xml.etree.ElementTree as ET

from filing_processing_evaluation.normalization.xhtml import visible_text

FITTED_STYLE = "white-space:pre-wrap;min-width:fit-content"


def test_visible_text_rejoins_continuous_fitted_span_fragments() -> None:
    element = ET.fromstring(
        f'<p><span style="{FITTED_STYLE}">ITEM 1. B</span>'
        f'<span style="{FITTED_STYLE}">USINESS</span></p>'
    )

    assert visible_text(element) == "ITEM 1. BUSINESS"


def test_visible_text_keeps_fallback_separators_for_independent_fragments() -> None:
    element = ET.fromstring(
        "<p><span>(2)</span><span>In May</span> "
        "<span>Greater</span><span>China</span></p>"
    )

    assert visible_text(element) == "(2) In May Greater China"
