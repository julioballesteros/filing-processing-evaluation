"""Parse loaded Inline XBRL bytes into an XHTML tree."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from filing_processing_evaluation.normalization.errors import NormalizationError
from filing_processing_evaluation.normalization.models import (
    Diagnostic,
    NormalizationInput,
    ParsedXhtml,
    StageOutcome,
)
from filing_processing_evaluation.normalization.xhtml import (
    XHTML_NAMESPACE,
    XML_LANGUAGE,
    visible_text,
)

INLINE_XBRL_NAMESPACE = "http://www.xbrl.org/2013/inlineXBRL"


class XhtmlParser:
    """Parse and validate the XHTML envelope without accessing storage."""

    stage_name = "parse_xhtml"

    def parse(self, source: NormalizationInput) -> StageOutcome[ParsedXhtml]:
        try:
            root = ET.fromstring(source.content)
        except ET.ParseError as error:
            raise NormalizationError(
                "raw filing is not valid XHTML",
                stage=self.stage_name,
                filing_id=source.metadata.filing_id,
            ) from error
        body = root.find(f"{{{XHTML_NAMESPACE}}}body")
        if body is None:
            raise NormalizationError(
                "raw filing does not contain an XHTML body",
                stage=self.stage_name,
                filing_id=source.metadata.filing_id,
            )
        title_element = root.find(
            f"{{{XHTML_NAMESPACE}}}head/{{{XHTML_NAMESPACE}}}title"
        )
        source_title = (
            visible_text(title_element)
            if title_element is not None
            else source.metadata.primary_document
        )
        language = root.attrib.get(XML_LANGUAGE, "en")
        inline_xbrl_elements = sum(
            descendant.tag.startswith(f"{{{INLINE_XBRL_NAMESPACE}}}")
            for descendant in root.iter()
        )
        diagnostic = Diagnostic(
            stage=self.stage_name,
            code="xhtml_parsed",
            message="Parsed the loaded filing as XHTML.",
            details={
                "source_bytes": len(source.content),
                "inline_xbrl_elements": inline_xbrl_elements,
            },
        )
        return StageOutcome(
            ParsedXhtml(
                body=body,
                source_title=source_title,
                language=language,
            ),
            (diagnostic,),
        )
