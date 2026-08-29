"""Orchestration service for deterministic filing normalization."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any

from filing_processing_evaluation.dataset import DatasetError, Filing
from filing_processing_evaluation.normalization.files import sha256_file
from filing_processing_evaluation.normalization.schema import SCHEMA_VERSION
from filing_processing_evaluation.normalization.sections import (
    NOTE_PATTERN,
    SectionDefinition,
    section_definition,
)
from filing_processing_evaluation.normalization.tables import normalize_table
from filing_processing_evaluation.normalization.validation import validate_normalized
from filing_processing_evaluation.normalization.xhtml import (
    TEXT_CONTAINER_TAGS,
    XHTML_NAMESPACE,
    XML_LANGUAGE,
    has_descendant_container,
    has_style,
    is_hidden,
    local_name,
    style_profile,
    visible_text,
)

PHONE_PATTERN = re.compile(r"^\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}$")
PAGE_FURNITURE_PATTERN = re.compile(r"\|\s+.*Form 10-[KQ]\s+\|\s+\d+$")


def _is_page_furniture(text: str) -> bool:
    return len(text) < 120 and PAGE_FURNITURE_PATTERN.search(text) is not None


class _DocumentNormalizer:
    """Build normalized blocks and sections while walking an XHTML body."""

    def __init__(self) -> None:
        self.blocks: list[dict[str, Any]] = []
        self.sections: list[dict[str, Any]] = []
        self.page_number = 1
        self._section_by_level: dict[int, str] = {}
        self._saw_nonitalic_major_heading = False
        self._inside_note = False
        self._note_has_nonitalic_subheading = False

    @property
    def current_section_id(self) -> str | None:
        if not self._section_by_level:
            return None
        return self._section_by_level[max(self._section_by_level)]

    def walk(self, element: ET.Element, path: str) -> None:
        if is_hidden(element):
            return
        tag = local_name(element.tag)
        if tag == "table":
            self._add_table(element, path)
            return
        if tag == "hr":
            self.page_number += 1
            return
        has_nested_blocks = has_descendant_container(element)
        if tag in TEXT_CONTAINER_TAGS and not has_nested_blocks:
            self._add_text(element, tag, path)
            return
        sibling_counts: dict[str, int] = defaultdict(int)
        for child in element:
            child_tag = local_name(child.tag)
            sibling_counts[child_tag] += 1
            self.walk(child, f"{path}/{child_tag}[{sibling_counts[child_tag]}]")

    def _next_block_id(self) -> str:
        return f"b{len(self.blocks) + 1:04d}"

    def _add_block(self, block_type: str, source_path: str, **fields: Any) -> str:
        block_id = self._next_block_id()
        block = {
            "id": block_id,
            "order": len(self.blocks) + 1,
            "section_id": self.current_section_id,
            "type": block_type,
            "source": {"page_number": self.page_number, "path": source_path},
            **fields,
        }
        self.blocks.append(block)
        return block_id

    def _start_section(
        self, definition: SectionDefinition, heading_block_id: str
    ) -> str:
        parent_levels = [
            level for level in self._section_by_level if level < definition.level
        ]
        parent_id = (
            self._section_by_level[max(parent_levels)] if parent_levels else None
        )
        section_id = f"s{len(self.sections) + 1:04d}"
        self.sections.append(
            {
                "id": section_id,
                "order": len(self.sections) + 1,
                "parent_id": parent_id,
                "level": definition.level,
                "label": definition.label,
                "title": definition.title,
                "heading_block_id": heading_block_id,
            }
        )
        self._section_by_level = {
            level: existing_id
            for level, existing_id in self._section_by_level.items()
            if level < definition.level
        }
        self._section_by_level[definition.level] = section_id
        self._saw_nonitalic_major_heading = False
        self._inside_note = False
        self._note_has_nonitalic_subheading = False
        return section_id

    def _add_text(self, element: ET.Element, tag: str, source_path: str) -> None:
        text = visible_text(element)
        if not text or _is_page_furniture(text):
            return
        definition = section_definition(text)
        profile = style_profile(element)
        is_centered = has_style(element, "text-align", "center")
        is_summary = any(
            "-sec-extract:summary" in descendant.attrib.get("style", "").lower()
            for descendant in element.iter()
        )
        word_count = len(text.split())
        is_phone = PHONE_PATTERN.fullmatch(text) is not None
        is_heading = definition is not None or (
            not is_phone
            and len(text) <= 300
            and (
                (profile.mostly_bold and (is_centered or is_summary or text.isupper()))
                or (profile.mostly_bold and word_count <= 45)
                or (
                    profile.mostly_italic
                    and ((profile.mostly_bold and word_count <= 45) or len(text) <= 120)
                )
            )
        )
        if is_heading:
            if definition is not None:
                level = definition.level
            elif NOTE_PATTERN.match(text):
                level = 3
                self._inside_note = True
                self._note_has_nonitalic_subheading = False
            elif self._inside_note:
                level = (
                    5
                    if profile.mostly_italic and self._note_has_nonitalic_subheading
                    else 4
                )
                if not profile.mostly_italic:
                    self._note_has_nonitalic_subheading = True
            elif profile.mostly_italic and not profile.mostly_bold:
                level = 4 if self._saw_nonitalic_major_heading else 3
            else:
                level = 3
            block_id = self._add_block("heading", source_path, level=level, text=text)
            if definition is not None:
                section_id = self._start_section(definition, block_id)
                self.blocks[-1]["section_id"] = section_id
            elif not profile.mostly_italic:
                self._saw_nonitalic_major_heading = True
            return
        block_type = "list_item" if tag == "li" else "paragraph"
        self._add_block(block_type, source_path, text=text)

    def _add_table(self, table: ET.Element, source_path: str) -> None:
        normalized = normalize_table(table)
        if normalized is not None:
            self._add_block("table", source_path, **normalized)


class NormalizationService:
    """Unified interface for converting one locked raw filing to normalized JSON."""

    def normalize(
        self, filing: Filing, *, dataset_dir: Path, expected_sha256: str
    ) -> dict[str, Any]:
        """Create a deterministic normalized draft for one downloaded filing."""
        raw_path = dataset_dir / filing.raw_path
        if not raw_path.is_file():
            raise DatasetError(f"raw artifact not found: {raw_path}")
        actual_sha256 = sha256_file(raw_path)
        if actual_sha256 != expected_sha256:
            raise DatasetError(f"raw artifact failed integrity check: {raw_path}")
        try:
            root = ET.parse(raw_path).getroot()
        except ET.ParseError as error:
            raise DatasetError(f"raw filing is not valid XHTML: {raw_path}") from error
        body = root.find(f"{{{XHTML_NAMESPACE}}}body")
        if body is None:
            raise DatasetError(f"raw filing does not contain an XHTML body: {raw_path}")
        title_element = root.find(
            f"{{{XHTML_NAMESPACE}}}head/{{{XHTML_NAMESPACE}}}title"
        )
        source_title = (
            visible_text(title_element)
            if title_element is not None
            else filing.primary_document
        )
        language = root.attrib.get(XML_LANGUAGE, "en")
        normalizer = _DocumentNormalizer()
        normalizer.walk(body, "/html/body[1]")
        if not normalizer.blocks:
            raise DatasetError(f"normalization produced no blocks: {raw_path}")
        document = {
            "schema_version": SCHEMA_VERSION,
            "filing_id": filing.filing_id,
            "raw_sha256": actual_sha256,
            "document": {
                "title": (
                    f"{filing.company_name} {filing.form_type} for period ended "
                    f"{filing.period_end_date}"
                ),
                "source_title": source_title,
                "language": language,
                "company_name": filing.company_name,
                "form_type": filing.form_type,
                "filing_date": filing.filing_date,
                "period_end_date": filing.period_end_date,
            },
            "page_count": normalizer.page_number,
            "sections": normalizer.sections,
            "blocks": normalizer.blocks,
        }
        validate_normalized(document)
        return document


_DEFAULT_SERVICE = NormalizationService()


def normalize_filing(
    filing: Filing, *, dataset_dir: Path, expected_sha256: str
) -> dict[str, Any]:
    """Normalize a filing through the default service instance."""
    return _DEFAULT_SERVICE.normalize(
        filing, dataset_dir=dataset_dir, expected_sha256=expected_sha256
    )
