"""Tests for filing-section heading recognition."""

import pytest

from filing_processing_evaluation.normalization.stages.sections import (
    EarningsReleaseSectionPolicy,
    SectionDefinition,
    section_definition,
)


def test_combined_running_header_does_not_become_an_item_title() -> None:
    assert section_definition("Item 1B, 1C") == SectionDefinition(
        level=2,
        label="Item 1B",
        title=None,
    )
    assert section_definition(
        "ITEM 1B. UNRESOLVED STAFF COMMENTS"
    ) == SectionDefinition(
        level=2,
        label="Item 1B",
        title="UNRESOLVED STAFF COMMENTS",
    )


@pytest.mark.parametrize(
    ("heading", "label"),
    [
        ("Financial Hi ghlights", "HIGHLIGHTS"),
        ("Quarterly Highlights, Product Releases, and Customer Stories", "HIGHLIGHTS"),
        ("Q2 Fiscal 2027 Summary", "RESULTS"),
        ("SECOND-QUARTER 2026 RESULTS 1", "RESULTS"),
        ("Operating Review \u2013 Three Months Ended July 3, 2026", "RESULTS"),
        ("Financial Guidance", "OUTLOOK"),
        ("Business Outlook", "OUTLOOK"),
        ("Conference Call and Webcast Information", "CONFERENCE CALL"),
        ("Webcast Details", "CONFERENCE CALL"),
        ("Frequently Used Terms and Non-GAAP Measures", "NON-GAAP"),
        ("Reconciliation of GAAP to Non-GAAP Financial Measures", "NON-GAAP"),
        (
            "CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS (Unaudited)",
            "FINANCIAL STATEMENTS",
        ),
        ("Forward-Looking Statements", "FORWARD-LOOKING"),
        ("About NVIDIA", "ABOUT"),
    ],
)
def test_earnings_release_section_policy_recognizes_corpus_headings(
    heading: str, label: str
) -> None:
    definition = EarningsReleaseSectionPolicy().definition(heading)

    assert definition is not None
    assert definition.label == label


def test_earnings_release_section_policy_does_not_promote_narrative_text() -> None:
    assert (
        EarningsReleaseSectionPolicy().definition(
            "Revenue was $37.8 billion, with the following business highlights:"
        )
        is None
    )
    assert (
        EarningsReleaseSectionPolicy().definition(
            "UnitedHealth Group reconciliation of non-GAAP financial measures "
            + "with supporting narrative " * 30
        )
        is None
    )
