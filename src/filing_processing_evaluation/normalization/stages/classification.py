"""Differentiate projected HTML elements and extract semantic signals."""

from __future__ import annotations

import re

from filing_processing_evaluation.normalization.models import (
    ClassifiedDocument,
    ClassifiedElement,
    ClassifiedTable,
    ClassifiedText,
    Diagnostic,
    ProjectedDocument,
    ProjectedTable,
    ProjectedText,
    StageOutcome,
    TextFeatures,
)
from filing_processing_evaluation.normalization.xhtml import (
    has_style,
    style_profile,
    visible_text,
)

PHONE_PATTERN = re.compile(r"^\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}$")
PAGE_FURNITURE_PATTERN = re.compile(r"\|\s+.*Form 10-[KQ]\s+\|\s+\d+$")


def _is_page_furniture(text: str) -> bool:
    return len(text) < 120 and PAGE_FURNITURE_PATTERN.search(text) is not None


class ElementClassifier:
    """Turn projected HTML elements into text and table candidates."""

    stage_name = "classify_elements"

    def classify(
        self, projected: ProjectedDocument
    ) -> StageOutcome[ClassifiedDocument]:
        elements: list[ClassifiedElement] = []
        discarded_empty_text = 0
        discarded_page_furniture = 0
        for value in projected.elements:
            if isinstance(value, ProjectedTable):
                elements.append(
                    ClassifiedTable(element=value.element, source=value.source)
                )
                continue
            assert isinstance(value, ProjectedText)
            text = visible_text(value.element)
            if not text:
                discarded_empty_text += 1
                continue
            if _is_page_furniture(text):
                discarded_page_furniture += 1
                continue
            profile = style_profile(value.element)
            features = TextFeatures(
                mostly_bold=profile.mostly_bold,
                mostly_italic=profile.mostly_italic,
                centered=has_style(value.element, "text-align", "center"),
                summary=any(
                    "-sec-extract:summary" in descendant.attrib.get("style", "").lower()
                    for descendant in value.element.iter()
                ),
                word_count=len(text.split()),
                is_phone=PHONE_PATTERN.fullmatch(text) is not None,
            )
            elements.append(
                ClassifiedText(
                    text=text,
                    tag=value.tag,
                    source=value.source,
                    features=features,
                )
            )
        text_elements = sum(isinstance(value, ClassifiedText) for value in elements)
        diagnostic = Diagnostic(
            stage=self.stage_name,
            code="elements_classified",
            message="Classified visible elements and extracted layout signals.",
            details={
                "text_candidates": text_elements,
                "table_candidates": len(elements) - text_elements,
                "discarded_empty_text": discarded_empty_text,
                "discarded_page_furniture": discarded_page_furniture,
            },
        )
        return StageOutcome(
            ClassifiedDocument(
                source_title=projected.source_title,
                language=projected.language,
                page_count=projected.page_count,
                elements=tuple(elements),
            ),
            (diagnostic,),
        )
