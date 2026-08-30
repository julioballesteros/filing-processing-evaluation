"""Form-specific SEC section policies."""

from filing_processing_evaluation.normalization.sections import (
    SectionDefinition,
    section_definition,
)


class TenKSectionPolicy:
    """Recognize the part and item hierarchy of an annual report."""

    def definition(self, text: str) -> SectionDefinition | None:
        return section_definition(text)


class TenQSectionPolicy:
    """Recognize the part and item hierarchy of a quarterly report."""

    def definition(self, text: str) -> SectionDefinition | None:
        return section_definition(text)
