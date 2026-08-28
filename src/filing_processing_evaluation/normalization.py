"""Deterministic baseline normalization for SEC Inline XBRL documents."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from bisect import bisect_left
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from filing_processing_evaluation.dataset import DatasetError, Filing

SCHEMA_VERSION = "1.1.0"
XHTML_NAMESPACE = "http://www.w3.org/1999/xhtml"
XML_LANGUAGE = "{http://www.w3.org/XML/1998/namespace}lang"
CONTAINER_TAGS = {"div", "p", "li", "ol", "ul", "table", "hr"}
TEXT_CONTAINER_TAGS = {"div", "p", "li"}
PART_PATTERN = re.compile(r"^PART\s+([IVX]+)(?:\s*[—-]\s*(.*))?$", re.IGNORECASE)
ITEM_PATTERN = re.compile(r"^Item\s+(\d+[A-Z]?)\.?\s*(.*)$", re.IGNORECASE)
NOTE_PATTERN = re.compile("^Note\\s+\\d+[A-Z]?\\s*[\\u2013\\u2014-]", re.IGNORECASE)
PHONE_PATTERN = re.compile(r"^\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}$")
PAGE_FURNITURE_PATTERN = re.compile(r"\|\s+.*Form 10-[KQ]\s+\|\s+\d+$")
FOOTNOTE_PATTERN = re.compile(r"^\([0-9A-Za-z]+\)$")
CURRENCY_SYMBOLS = {"$", "€", "£", "¥"}
NORMALIZED_MANIFEST_FIELDS = {
    "filing_id",
    "path",
    "sha256",
    "schema_version",
    "status",
    "reviewed_at",
    "reviewed_by",
}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def normalize_text(value: str) -> str:
    """Normalize Unicode and whitespace without changing content."""
    normalized = unicodedata.normalize("NFC", value).replace("\u00a0", " ")
    return " ".join(normalized.split())


def _needs_fragment_separator(left: str, right: str) -> bool:
    left_word = left.isalnum() or left in ")]%}\u201d\u2019"
    right_word = right.isalnum() or right in "([\u201c\u2018"
    return left_word and right_word


def _join_fragments(fragments: list[str]) -> str:
    combined = ""
    for fragment in fragments:
        if not fragment:
            continue
        if (
            combined
            and not combined[-1].isspace()
            and not fragment[0].isspace()
            and _needs_fragment_separator(combined[-1], fragment[0])
        ):
            combined += " "
        combined += fragment
    return normalize_text(combined)


def _is_hidden(element: ET.Element) -> bool:
    style = element.attrib.get("style", "").replace(" ", "").lower()
    return "display:none" in style or "visibility:hidden" in style


def _visible_text(element: ET.Element) -> str:
    fragments: list[str] = []

    def collect(node: ET.Element) -> None:
        if _is_hidden(node):
            return
        if node.text:
            fragments.append(node.text)
        for child in node:
            if _local_name(child.tag) == "br":
                fragments.append(" ")
            else:
                collect(child)
            if child.tail:
                fragments.append(child.tail)

    collect(element)
    return _join_fragments(fragments)


def _has_descendant_container(element: ET.Element) -> bool:
    return any(
        _local_name(descendant.tag) in CONTAINER_TAGS
        for descendant in list(element.iter())[1:]
    )


def _style_properties(element: ET.Element) -> dict[str, str]:
    properties: dict[str, str] = {}
    for declaration in element.attrib.get("style", "").split(";"):
        if ":" not in declaration:
            continue
        name, value = declaration.split(":", 1)
        properties[name.strip().lower()] = value.strip().lower()
    return properties


def _has_style(element: ET.Element, name: str, value: str) -> bool:
    return any(
        _style_properties(descendant).get(name) == value
        for descendant in element.iter()
    )


@dataclass(frozen=True)
class StyleProfile:
    """Proportion of visible characters carrying inherited emphasis."""

    characters: int
    bold_characters: int
    italic_characters: int

    @property
    def mostly_bold(self) -> bool:
        return self.characters > 0 and self.bold_characters / self.characters >= 0.9

    @property
    def mostly_italic(self) -> bool:
        return self.characters > 0 and self.italic_characters / self.characters >= 0.9


def _style_profile(element: ET.Element) -> StyleProfile:
    characters = 0
    bold_characters = 0
    italic_characters = 0

    def add_text(value: str | None, *, bold: bool, italic: bool) -> None:
        nonlocal characters, bold_characters, italic_characters
        count = sum(not character.isspace() for character in value or "")
        characters += count
        bold_characters += count if bold else 0
        italic_characters += count if italic else 0

    def collect(
        node: ET.Element, *, inherited_bold: bool, inherited_italic: bool
    ) -> None:
        if _is_hidden(node):
            return
        properties = _style_properties(node)
        tag = _local_name(node.tag)
        bold = inherited_bold or tag in {"b", "strong"}
        italic = inherited_italic or tag in {"em", "i"}
        if weight := properties.get("font-weight"):
            try:
                bold = int(weight) >= 600
            except ValueError:
                bold = weight in {"bold", "bolder"}
        if font_style := properties.get("font-style"):
            italic = font_style in {"italic", "oblique"}
        add_text(node.text, bold=bold, italic=italic)
        for child in node:
            collect(child, inherited_bold=bold, inherited_italic=italic)
            add_text(child.tail, bold=bold, italic=italic)

    collect(element, inherited_bold=False, inherited_italic=False)
    return StyleProfile(characters, bold_characters, italic_characters)


def _is_page_furniture(text: str) -> bool:
    return len(text) < 120 and PAGE_FURNITURE_PATTERN.search(text) is not None


def _positive_int(value: str | None, default: int = 1) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class SectionDefinition:
    """A recognized SEC section heading."""

    level: int
    label: str
    title: str | None


@dataclass(frozen=True)
class PhysicalCell:
    """One source HTML table cell before logical-grid normalization."""

    row: int
    column: int
    row_span: int
    column_span: int
    text: str
    source_header: bool
    styled_header: bool

    @property
    def end_column(self) -> int:
        return self.column + self.column_span

    def source_mapping(self) -> dict[str, int]:
        return {
            "row": self.row,
            "column": self.column,
            "row_span": self.row_span,
            "column_span": self.column_span,
        }


def _section_definition(text: str) -> SectionDefinition | None:
    if match := PART_PATTERN.fullmatch(text):
        label = f"PART {match.group(1).upper()}"
        title = match.group(2).strip(" .") if match.group(2) else None
        return SectionDefinition(level=1, label=label, title=title)
    if match := ITEM_PATTERN.match(text):
        label = f"Item {match.group(1).upper()}"
        title = match.group(2).strip(" .") or None
        return SectionDefinition(level=2, label=label, title=title)
    if text.upper() == "SIGNATURE":
        return SectionDefinition(level=1, label="SIGNATURE", title=None)
    return None


def _table_rows(table: ET.Element) -> list[ET.Element]:
    rows: list[ET.Element] = []

    def collect(node: ET.Element) -> None:
        for child in node:
            tag = _local_name(child.tag)
            if tag == "table":
                continue
            if tag == "tr":
                rows.append(child)
            else:
                collect(child)

    collect(table)
    return rows


def _extract_physical_cells(
    table: ET.Element,
) -> tuple[list[PhysicalCell], int, int]:
    rows = _table_rows(table)
    cells: list[PhysicalCell] = []
    active_row_spans: dict[int, int] = {}
    column_count = 0
    for row_index, row in enumerate(rows):
        column = 0
        for cell in row:
            tag = _local_name(cell.tag)
            if tag not in {"td", "th"}:
                continue
            row_span = _positive_int(cell.attrib.get("rowspan"))
            column_span = _positive_int(cell.attrib.get("colspan"))
            while any(
                active_row_spans.get(occupied, 0) > 0
                for occupied in range(column, column + column_span)
            ):
                column += 1
            profile = _style_profile(cell)
            centered = _has_style(cell, "text-align", "center")
            cells.append(
                PhysicalCell(
                    row=row_index,
                    column=column,
                    row_span=row_span,
                    column_span=column_span,
                    text=_visible_text(cell),
                    source_header=tag == "th",
                    styled_header=profile.mostly_bold and centered,
                )
            )
            if row_span > 1:
                for occupied in range(column, column + column_span):
                    active_row_spans[occupied] = row_span
            column += column_span
        column_count = max(column_count, column)
        active_row_spans = {
            occupied: remaining - 1
            for occupied, remaining in active_row_spans.items()
            if remaining > 1
        }
    return cells, len(rows), column_count


def _numeric_like(text: str) -> bool:
    compact = text.strip().replace(" ", "")
    return re.fullmatch(
        r"[$€£¥]?\(?-?[0-9][0-9,]*(?:\.[0-9]+)?%?\)?", compact
    ) is not None or compact in {"—", "-", "N/A"}


def _merge_currency_cells(cells: list[PhysicalCell]) -> list[dict[str, Any]]:
    nonempty = [
        cell for cell in sorted(cells, key=lambda value: value.column) if cell.text
    ]
    merged: list[dict[str, Any]] = []
    index = 0
    while index < len(nonempty):
        cell = nonempty[index]
        candidate: dict[str, Any] = {
            "physical_row": cell.row,
            "column": cell.column,
            "end_column": cell.end_column,
            "row_span": cell.row_span,
            "text": cell.text,
            "source_header": cell.source_header,
            "styled_header": cell.styled_header,
            "source_cells": [cell.source_mapping()],
        }
        if cell.text in CURRENCY_SYMBOLS and index + 1 < len(nonempty):
            following = nonempty[index + 1]
            if following.column <= cell.end_column + 1 and _numeric_like(
                following.text
            ):
                candidate["end_column"] = following.end_column
                candidate["row_span"] = max(cell.row_span, following.row_span)
                candidate["text"] = cell.text + following.text
                candidate["source_header"] = (
                    cell.source_header or following.source_header
                )
                candidate["styled_header"] = (
                    cell.styled_header or following.styled_header
                )
                candidate["source_cells"].append(following.source_mapping())
                index += 1
        if index + 1 < len(nonempty):
            suffix = nonempty[index + 1]
            if (
                suffix.text == "%"
                and suffix.column <= int(candidate["end_column"]) + 1
                and _numeric_like(str(candidate["text"]))
            ):
                candidate["end_column"] = suffix.end_column
                candidate["text"] = str(candidate["text"]) + suffix.text
                candidate["source_header"] = (
                    candidate["source_header"] or suffix.source_header
                )
                candidate["styled_header"] = (
                    candidate["styled_header"] or suffix.styled_header
                )
                candidate["source_cells"].append(suffix.source_mapping())
                index += 1
        merged.append(candidate)
        index += 1
    return merged


def _logical_column_anchors(rows: Mapping[int, list[dict[str, Any]]]) -> list[int]:
    candidates = [cell for row in rows.values() for cell in row]
    start_counts = Counter(int(cell["column"]) for cell in candidates)
    anchors = {
        start
        for start, count in start_counts.items()
        if count >= 2
        or any(
            int(cell["column"]) == start
            and (cell["source_header"] or cell["styled_header"])
            and FOOTNOTE_PATTERN.fullmatch(str(cell["text"])) is None
            for cell in candidates
        )
    }
    if rows:
        widest = max(rows.values(), key=len)
        anchors.update(
            int(cell["column"])
            for cell in widest
            if FOOTNOTE_PATTERN.fullmatch(str(cell["text"])) is None
        )
    if candidates:
        anchors.add(min(int(cell["column"]) for cell in candidates))
    if len(anchors) < 2:
        anchors.update(int(cell["column"]) for cell in candidates)
    return sorted(anchors)


def _combine_cell_text(left: str, right: str) -> str:
    if left in CURRENCY_SYMBOLS or right.startswith(tuple(CURRENCY_SYMBOLS)):
        return left + right
    return normalize_text(f"{left} {right}")


def _nearest_anchor_index(anchors: list[int], physical_column: int) -> int:
    """Map a spacer-grid position to its visually nearest logical column."""
    insertion = bisect_left(anchors, physical_column)
    if insertion == 0:
        return 0
    if insertion == len(anchors):
        return len(anchors) - 1
    previous = anchors[insertion - 1]
    following = anchors[insertion]
    if physical_column - previous < following - physical_column:
        return insertion - 1
    return insertion


def _normalize_table(table: ET.Element) -> dict[str, Any] | None:
    physical_cells, physical_rows, physical_columns = _extract_physical_cells(table)
    if not any(physical_cell.text for physical_cell in physical_cells):
        return None
    by_physical_row: dict[int, list[PhysicalCell]] = defaultdict(list)
    for physical_cell in physical_cells:
        by_physical_row[physical_cell.row].append(physical_cell)
    rows = {
        row: merged
        for row, cells in by_physical_row.items()
        if (merged := _merge_currency_cells(cells))
    }
    kept_rows = sorted(rows)
    row_map = {physical: logical for logical, physical in enumerate(kept_rows)}
    anchors = _logical_column_anchors(rows)
    if not anchors:
        return None

    logical_cells: list[dict[str, Any]] = []
    for physical_row in kept_rows:
        grouped: dict[int, dict[str, Any]] = {}
        for candidate_cell in rows[physical_row]:
            physical_column = int(candidate_cell["column"])
            physical_end = int(candidate_cell["end_column"])
            column = _nearest_anchor_index(anchors, physical_column)
            end = max(column + 1, bisect_left(anchors, physical_end))
            covered_rows = [
                row
                for row in kept_rows
                if physical_row <= row < physical_row + int(candidate_cell["row_span"])
            ]
            value: dict[str, Any] = {
                "row": row_map[physical_row],
                "column": column,
                "row_span": max(1, len(covered_rows)),
                "column_span": max(1, end - column),
                "text": str(candidate_cell["text"]),
                "source_header": bool(candidate_cell["source_header"]),
                "styled_header": bool(candidate_cell["styled_header"]),
                "source_cells": list(candidate_cell["source_cells"]),
            }
            if existing := grouped.get(column):
                existing["text"] = _combine_cell_text(
                    str(existing["text"]), str(value["text"])
                )
                existing["row_span"] = max(
                    int(existing["row_span"]), int(value["row_span"])
                )
                existing["column_span"] = max(
                    int(existing["column_span"]), int(value["column_span"])
                )
                existing["source_header"] = (
                    existing["source_header"] or value["source_header"]
                )
                existing["styled_header"] = (
                    existing["styled_header"] or value["styled_header"]
                )
                existing["source_cells"].extend(value["source_cells"])
            else:
                grouped[column] = value
        logical_cells.extend(grouped[column] for column in sorted(grouped))

    cells_by_row: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for logical_cell in logical_cells:
        cells_by_row[int(logical_cell["row"])].append(logical_cell)
    data_rows = [
        row
        for row, cells in cells_by_row.items()
        if any(
            int(cell["column"]) == 0 and not _numeric_like(str(cell["text"]))
            for cell in cells
        )
        and any(
            int(cell["column"]) > 0 and _numeric_like(str(cell["text"]))
            for cell in cells
        )
    ]
    if data_rows:
        first_data_row = min(data_rows)
    elif len(kept_rows) > 1 and any(
        cell["source_header"] or cell["styled_header"] for cell in cells_by_row[0]
    ):
        first_data_row = 1
    else:
        first_data_row = 0

    for logical_cell in logical_cells:
        row = int(logical_cell["row"])
        column = int(logical_cell["column"])
        text = str(logical_cell["text"])
        source_header = bool(logical_cell.pop("source_header"))
        logical_cell.pop("styled_header")
        if row < first_data_row or source_header:
            role = "header"
        elif column == 0 and not _numeric_like(text):
            role = "row_header"
        else:
            role = "data"
        logical_cell["role"] = role

    caption = next(
        (
            _visible_text(descendant)
            for descendant in table
            if _local_name(descendant.tag) == "caption"
        ),
        None,
    )
    return {
        "caption": caption or None,
        "row_count": len(kept_rows),
        "column_count": len(anchors),
        "source_shape": {"rows": physical_rows, "columns": physical_columns},
        "cells": sorted(
            logical_cells, key=lambda cell: (int(cell["row"]), int(cell["column"]))
        ),
    }


class _DocumentNormalizer:
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
        if _is_hidden(element):
            return
        tag = _local_name(element.tag)
        if tag == "table":
            self._add_table(element, path)
            return
        if tag == "hr":
            self.page_number += 1
            return
        has_nested_blocks = _has_descendant_container(element)
        if tag in TEXT_CONTAINER_TAGS and not has_nested_blocks:
            self._add_text(element, tag, path)
            return
        sibling_counts: dict[str, int] = defaultdict(int)
        for child in element:
            child_tag = _local_name(child.tag)
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
        text = _visible_text(element)
        if not text or _is_page_furniture(text):
            return
        definition = _section_definition(text)
        profile = _style_profile(element)
        is_centered = _has_style(element, "text-align", "center")
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
        normalized = _normalize_table(table)
        if normalized is not None:
            self._add_block("table", source_path, **normalized)


def normalize_filing(
    filing: Filing, *, dataset_dir: Path, expected_sha256: str
) -> dict[str, Any]:
    """Create a deterministic normalized draft for one downloaded filing."""
    raw_path = dataset_dir / filing.raw_path
    if not raw_path.is_file():
        raise DatasetError(f"raw artifact not found: {raw_path}")
    actual_sha256 = _sha256(raw_path)
    if actual_sha256 != expected_sha256:
        raise DatasetError(f"raw artifact failed integrity check: {raw_path}")
    try:
        root = ET.parse(raw_path).getroot()
    except ET.ParseError as error:
        raise DatasetError(f"raw filing is not valid XHTML: {raw_path}") from error
    body = root.find(f"{{{XHTML_NAMESPACE}}}body")
    if body is None:
        raise DatasetError(f"raw filing does not contain an XHTML body: {raw_path}")
    title_element = root.find(f"{{{XHTML_NAMESPACE}}}head/{{{XHTML_NAMESPACE}}}title")
    source_title = (
        _visible_text(title_element)
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


def validate_normalized(document: Mapping[str, Any]) -> None:
    """Validate normalized-document referential and semantic invariants."""
    required = {
        "schema_version",
        "filing_id",
        "raw_sha256",
        "document",
        "page_count",
        "sections",
        "blocks",
    }
    if set(document) != required or document.get("schema_version") != SCHEMA_VERSION:
        raise DatasetError("normalized document has an unsupported schema")
    metadata = document.get("document")
    sections = document.get("sections")
    blocks = document.get("blocks")
    page_count = document.get("page_count")
    if not isinstance(metadata, dict):
        raise DatasetError("normalized document metadata must be an object")
    metadata_fields = {
        "title",
        "source_title",
        "language",
        "company_name",
        "form_type",
        "filing_date",
        "period_end_date",
    }
    if set(metadata) != metadata_fields or not all(
        isinstance(metadata[field], str) and metadata[field]
        for field in metadata_fields
    ):
        raise DatasetError("normalized document metadata has invalid fields")
    if metadata["form_type"] not in {"10-K", "10-Q"}:
        raise DatasetError("normalized document has an unsupported form type")
    filing_id = document.get("filing_id")
    raw_sha256 = document.get("raw_sha256")
    if not isinstance(filing_id, str) or not filing_id:
        raise DatasetError("normalized document has an invalid filing ID")
    if (
        not isinstance(raw_sha256, str)
        or len(raw_sha256) != 64
        or any(character not in "0123456789abcdef" for character in raw_sha256)
    ):
        raise DatasetError("normalized document has an invalid raw SHA-256")
    if not isinstance(sections, list) or not isinstance(blocks, list):
        raise DatasetError("normalized document sections and blocks must be arrays")
    if not isinstance(page_count, int) or page_count < 1:
        raise DatasetError("normalized document page_count must be positive")
    if not blocks:
        raise DatasetError("normalized document must contain blocks")

    block_ids: set[str] = set()
    block_by_id: dict[str, Mapping[str, Any]] = {}
    previous_page = 0
    for expected_order, block_value in enumerate(blocks, 1):
        if not isinstance(block_value, dict):
            raise DatasetError("normalized block must be an object")
        block = block_value
        block_id = block.get("id")
        if not isinstance(block_id, str) or block_id in block_ids:
            raise DatasetError("normalized block IDs must be unique strings")
        block_ids.add(block_id)
        block_by_id[block_id] = block
        if block.get("order") != expected_order:
            raise DatasetError("normalized block order must be contiguous")
        if block.get("type") not in {"heading", "paragraph", "list_item", "table"}:
            raise DatasetError(f"normalized block {block_id} has an invalid type")
        source = block.get("source")
        if not isinstance(source, dict):
            raise DatasetError(f"normalized block {block_id} has no source locator")
        page_number = source.get("page_number")
        path = source.get("path")
        if (
            not isinstance(page_number, int)
            or not 1 <= page_number <= page_count
            or page_number < previous_page
            or not isinstance(path, str)
            or not path.startswith("/html/body[1]/")
        ):
            raise DatasetError(f"normalized block {block_id} has invalid provenance")
        previous_page = page_number
        if block.get("type") == "table":
            _validate_table(block)

    section_ids: set[str] = set()
    for expected_order, section_value in enumerate(sections, 1):
        if not isinstance(section_value, dict):
            raise DatasetError("normalized section must be an object")
        section = section_value
        section_id = section.get("id")
        if not isinstance(section_id, str) or section_id in section_ids:
            raise DatasetError("normalized section IDs must be unique strings")
        if section.get("order") != expected_order:
            raise DatasetError("normalized section order must be contiguous")
        parent_id = section.get("parent_id")
        if parent_id is not None and parent_id not in section_ids:
            raise DatasetError(f"normalized section {section_id} has invalid parent")
        heading = block_by_id.get(str(section.get("heading_block_id")))
        if heading is None or heading.get("type") != "heading":
            raise DatasetError(f"normalized section {section_id} has invalid heading")
        section_ids.add(section_id)
    if any(
        block.get("section_id") is not None
        and block.get("section_id") not in section_ids
        for block in blocks
    ):
        raise DatasetError("normalized block references an unknown section")


def _validate_table(table: Mapping[str, Any]) -> None:
    block_id = table.get("id", "table")
    row_count = table.get("row_count")
    column_count = table.get("column_count")
    cells = table.get("cells")
    if (
        not isinstance(row_count, int)
        or row_count < 1
        or not isinstance(column_count, int)
        or column_count < 1
        or not isinstance(cells, list)
        or not cells
    ):
        raise DatasetError(f"normalized table {block_id} has invalid shape")
    source_shape = table.get("source_shape")
    if (
        not isinstance(source_shape, dict)
        or not isinstance(source_shape.get("rows"), int)
        or not isinstance(source_shape.get("columns"), int)
        or source_shape["rows"] < 1
        or source_shape["columns"] < 1
    ):
        raise DatasetError(f"normalized table {block_id} has invalid source shape")
    occupied: set[tuple[int, int]] = set()
    for cell in cells:
        if not isinstance(cell, dict) or not isinstance(cell.get("text"), str):
            raise DatasetError(f"normalized table {block_id} has an invalid cell")
        if not cell["text"]:
            raise DatasetError(f"normalized table {block_id} contains an empty cell")
        if cell.get("role") not in {"header", "row_header", "data"}:
            raise DatasetError(f"normalized table {block_id} has an invalid cell role")
        source_cells = cell.get("source_cells")
        if not isinstance(source_cells, list) or not source_cells:
            raise DatasetError(
                f"normalized table {block_id} cell has no source coordinates"
            )
        try:
            row = int(cell["row"])
            column = int(cell["column"])
            row_span = int(cell["row_span"])
            column_span = int(cell["column_span"])
        except (KeyError, TypeError, ValueError) as error:
            raise DatasetError(
                f"normalized table {block_id} has invalid coordinates"
            ) from error
        if (
            row < 0
            or column < 0
            or row_span < 1
            or column_span < 1
            or row + row_span > row_count
            or column + column_span > column_count
        ):
            raise DatasetError(f"normalized table {block_id} cell is out of bounds")
        for position in (
            (candidate_row, candidate_column)
            for candidate_row in range(row, row + row_span)
            for candidate_column in range(column, column + column_span)
        ):
            if position in occupied:
                raise DatasetError(f"normalized table {block_id} cells overlap")
            occupied.add(position)


def write_normalized(document: dict[str, Any], path: Path) -> None:
    """Write a normalized document deterministically and atomically."""
    validate_normalized(document)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def load_normalized(path: Path) -> dict[str, Any]:
    """Load and semantically validate a normalized document."""
    if not path.is_file():
        raise DatasetError(f"normalized document not found: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise DatasetError(f"normalized document is invalid JSON: {path}") from error
    if not isinstance(value, dict):
        raise DatasetError(f"normalized document must be a JSON object: {path}")
    try:
        validate_normalized(value)
    except DatasetError as error:
        raise DatasetError(f"{error}: {path}") from error
    return value


def _load_normalized_manifest(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    entries: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise DatasetError(
                f"normalized manifest line {line_number} is invalid JSON"
            ) from error
        if not isinstance(value, dict) or set(value) != NORMALIZED_MANIFEST_FIELDS:
            raise DatasetError(
                f"normalized manifest line {line_number} has invalid fields"
            )
        filing_id = value.get("filing_id")
        if not isinstance(filing_id, str) or filing_id in entries:
            raise DatasetError("normalized manifest filing IDs must be unique")
        status = value.get("status")
        reviewed_at = value.get("reviewed_at")
        reviewed_by = value.get("reviewed_by")
        sha256 = value.get("sha256")
        if (
            value.get("schema_version") != SCHEMA_VERSION
            or status not in {"draft", "reviewed"}
            or not isinstance(value.get("path"), str)
            or not str(value["path"]).startswith("normalized/")
            or not isinstance(sha256, str)
            or len(sha256) != 64
        ):
            raise DatasetError(
                f"normalized manifest line {line_number} has invalid values"
            )
        if (
            status == "draft" and (reviewed_at is not None or reviewed_by is not None)
        ) or (
            status == "reviewed"
            and (
                not isinstance(reviewed_at, str)
                or not isinstance(reviewed_by, str)
                or not reviewed_by
            )
        ):
            raise DatasetError(
                f"normalized manifest line {line_number} has invalid review state"
            )
        entries[filing_id] = value
    return entries


def update_normalized_manifest(
    *,
    dataset_dir: Path,
    normalized_path: Path,
    document: Mapping[str, Any],
    reviewer: str | None = None,
    reviewed_at: str | None = None,
) -> Path:
    """Record a normalized artifact hash and review state atomically."""
    try:
        relative_path = normalized_path.resolve().relative_to(dataset_dir.resolve())
    except ValueError as error:
        raise DatasetError(
            "normalized artifact must be inside the dataset directory"
        ) from error
    if not relative_path.parts or relative_path.parts[0] != "normalized":
        raise DatasetError("normalized artifact path must be below normalized/")
    if not normalized_path.is_file():
        raise DatasetError(f"normalized document not found: {normalized_path}")
    filing_id = str(document["filing_id"])
    manifest_path = dataset_dir / "normalized" / "manifest.jsonl"
    entries = _load_normalized_manifest(manifest_path)
    artifact_hash = _sha256(normalized_path)
    previous = entries.get(filing_id)
    if reviewer is None:
        if (
            previous is not None
            and previous.get("sha256") == artifact_hash
            and previous.get("status") == "reviewed"
        ):
            status = "reviewed"
            entry_reviewer = previous.get("reviewed_by")
            entry_reviewed_at = previous.get("reviewed_at")
        else:
            status = "draft"
            entry_reviewer = None
            entry_reviewed_at = None
    else:
        if not reviewer.strip():
            raise DatasetError("reviewer must not be empty")
        status = "reviewed"
        entry_reviewer = reviewer.strip()
        entry_reviewed_at = reviewed_at or datetime.now(UTC).isoformat().replace(
            "+00:00", "Z"
        )
    entries[filing_id] = {
        "filing_id": filing_id,
        "path": relative_path.as_posix(),
        "sha256": artifact_hash,
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reviewed_at": entry_reviewed_at,
        "reviewed_by": entry_reviewer,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest_path.with_suffix(".jsonl.tmp")
    temporary.write_text(
        "".join(
            json.dumps(entries[key], sort_keys=True) + "\n" for key in sorted(entries)
        ),
        encoding="utf-8",
    )
    temporary.replace(manifest_path)
    return manifest_path
