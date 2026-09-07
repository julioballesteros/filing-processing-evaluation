"""Parse strict XHTML and SEC-wrapped HTML into a shared element tree."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import cast

import html5lib  # type: ignore[import-untyped]

from filing_processing_evaluation.normalization.errors import NormalizationError
from filing_processing_evaluation.normalization.models import (
    Diagnostic,
    DocumentInput,
    ParsedXhtml,
    StageOutcome,
)
from filing_processing_evaluation.normalization.xhtml import (
    XHTML_NAMESPACE,
    XML_LANGUAGE,
    visible_text,
)

INLINE_XBRL_NAMESPACE = "http://www.xbrl.org/2013/inlineXBRL"
SEC_TEXT_OPEN = b"<TEXT>"
SEC_TEXT_CLOSE = b"</TEXT>"


def _parsed_outcome(
    root: ET.Element,
    source: DocumentInput,
    *,
    stage_name: str,
    code: str,
    message: str,
    parse_errors: int = 0,
) -> StageOutcome[ParsedXhtml]:
    body = root.find(f"{{{XHTML_NAMESPACE}}}body")
    if body is None:
        raise NormalizationError(
            "raw filing does not contain an HTML body",
            stage=stage_name,
            filing_id=source.metadata.filing_id,
        )
    title_element = root.find(f"{{{XHTML_NAMESPACE}}}head/{{{XHTML_NAMESPACE}}}title")
    source_title = (
        visible_text(title_element)
        if title_element is not None
        else source.artifact.filename
    )
    language = root.attrib.get(XML_LANGUAGE, root.attrib.get("lang", "en"))
    inline_xbrl_elements = sum(
        descendant.tag.startswith(f"{{{INLINE_XBRL_NAMESPACE}}}")
        for descendant in root.iter()
        if isinstance(descendant.tag, str)
    )
    diagnostic = Diagnostic(
        stage=stage_name,
        code=code,
        message=message,
        details={
            "source_bytes": len(source.content),
            "inline_xbrl_elements": inline_xbrl_elements,
            "parse_errors": parse_errors,
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


def _unwrap_sec_text(content: bytes, source: DocumentInput) -> bytes:
    upper = content.upper()
    start = upper.find(SEC_TEXT_OPEN)
    if start < 0:
        return content
    end = upper.rfind(SEC_TEXT_CLOSE)
    start += len(SEC_TEXT_OPEN)
    if end < start:
        raise NormalizationError(
            "SEC document wrapper has no closing TEXT element",
            stage=SecHtmlParser.stage_name,
            filing_id=source.metadata.filing_id,
        )
    payload = content[start:end].strip()
    if not payload:
        raise NormalizationError(
            "SEC document wrapper contains no HTML",
            stage=SecHtmlParser.stage_name,
            filing_id=source.metadata.filing_id,
        )
    return payload


def _remove_non_element_nodes(root: ET.Element) -> None:
    """Remove comments emitted as callable-tag nodes by the etree HTML builder."""
    for parent in root.iter():
        for child in list(parent):
            if not isinstance(child.tag, str):
                parent.remove(child)


class XhtmlParser:
    """Parse and validate the XHTML envelope without accessing storage."""

    stage_name = "parse_xhtml"

    def parse(self, source: DocumentInput) -> StageOutcome[ParsedXhtml]:
        try:
            root = ET.fromstring(source.content)
        except ET.ParseError as error:
            raise NormalizationError(
                "raw filing is not valid XHTML",
                stage=self.stage_name,
                filing_id=source.metadata.filing_id,
            ) from error
        return _parsed_outcome(
            root,
            source,
            stage_name=self.stage_name,
            code="xhtml_parsed",
            message="Parsed the selected raw artifact as XHTML.",
        )


class SecHtmlParser:
    """Unwrap and tolerantly parse an HTML document from an SEC submission."""

    stage_name = "parse_sec_html"

    def parse(self, source: DocumentInput) -> StageOutcome[ParsedXhtml]:
        content = _unwrap_sec_text(source.content, source)
        parser = html5lib.HTMLParser(
            tree=html5lib.getTreeBuilder("etree"),
            namespaceHTMLElements=True,
        )
        try:
            root = cast(ET.Element, parser.parse(content))
        except (TypeError, ValueError) as error:
            raise NormalizationError(
                "raw exhibit is not valid HTML",
                stage=self.stage_name,
                filing_id=source.metadata.filing_id,
            ) from error
        _remove_non_element_nodes(root)
        return _parsed_outcome(
            root,
            source,
            stage_name=self.stage_name,
            code="sec_html_parsed",
            message="Unwrapped and parsed the selected SEC HTML artifact.",
            parse_errors=len(parser.errors),
        )
