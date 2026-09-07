"""Project visible HTML elements out of an Inline XBRL tree."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict

from filing_processing_evaluation.normalization.models import (
    Diagnostic,
    ParsedXhtml,
    ProjectedDocument,
    ProjectedElement,
    ProjectedTable,
    ProjectedText,
    SourceLocation,
    StageOutcome,
)
from filing_processing_evaluation.normalization.xhtml import (
    TEXT_CONTAINER_TAGS,
    has_descendant_container,
    is_hidden,
    local_name,
)


class InlineXbrlProjector:
    """Keep visible HTML content while dropping hidden XBRL metadata subtrees."""

    stage_name = "project_visible_html"

    def project(self, parsed: ParsedXhtml) -> StageOutcome[ProjectedDocument]:
        elements: list[ProjectedElement] = []
        page_number = 1
        hidden_subtrees = 0
        page_breaks = 0
        image_elements = 0

        def walk(node: ET.Element, path: str) -> None:
            nonlocal page_number, hidden_subtrees, page_breaks, image_elements
            if is_hidden(node):
                hidden_subtrees += 1
                return
            tag = local_name(node.tag)
            source = SourceLocation(page_number=page_number, path=path)
            if tag == "img":
                image_elements += 1
            if tag == "table":
                elements.append(ProjectedTable(element=node, source=source))
                return
            if tag == "hr":
                page_number += 1
                page_breaks += 1
                return
            if tag in TEXT_CONTAINER_TAGS and not has_descendant_container(node):
                elements.append(ProjectedText(element=node, tag=tag, source=source))
                return
            sibling_counts: dict[str, int] = defaultdict(int)
            for child in node:
                child_tag = local_name(child.tag)
                sibling_counts[child_tag] += 1
                walk(child, f"{path}/{child_tag}[{sibling_counts[child_tag]}]")

        walk(parsed.body, "/html/body[1]")
        text_elements = sum(isinstance(value, ProjectedText) for value in elements)
        table_elements = len(elements) - text_elements
        diagnostic = Diagnostic(
            stage=self.stage_name,
            code="visible_html_projected",
            message="Projected visible block-level HTML from Inline XBRL.",
            details={
                "hidden_subtrees": hidden_subtrees,
                "text_elements": text_elements,
                "table_elements": table_elements,
                "image_elements": image_elements,
                "page_breaks": page_breaks,
            },
        )
        return StageOutcome(
            ProjectedDocument(
                source_title=parsed.source_title,
                language=parsed.language,
                page_count=page_number,
                elements=tuple(elements),
            ),
            (diagnostic,),
        )
