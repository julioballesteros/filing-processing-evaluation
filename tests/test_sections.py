"""Tests for filing-section heading recognition."""

from filing_processing_evaluation.normalization.stages.sections import (
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
