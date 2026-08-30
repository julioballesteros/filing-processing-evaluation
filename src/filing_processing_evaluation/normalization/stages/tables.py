"""Table normalization used by the block-building stage."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from bisect import bisect_left
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from filing_processing_evaluation.normalization.xhtml import (
    has_style,
    local_name,
    normalize_text,
    style_profile,
    visible_text,
)

FOOTNOTE_PATTERN = re.compile(r"^\([0-9A-Za-z]+\)$")
CURRENCY_SYMBOLS = {"$", "€", "£", "¥"}


def _positive_int(value: str | None, default: int = 1) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


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


def _table_rows(table: ET.Element) -> list[ET.Element]:
    rows: list[ET.Element] = []

    def collect(node: ET.Element) -> None:
        for child in node:
            tag = local_name(child.tag)
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
            tag = local_name(cell.tag)
            if tag not in {"td", "th"}:
                continue
            row_span = _positive_int(cell.attrib.get("rowspan"))
            column_span = _positive_int(cell.attrib.get("colspan"))
            while any(
                active_row_spans.get(occupied, 0) > 0
                for occupied in range(column, column + column_span)
            ):
                column += 1
            profile = style_profile(cell)
            centered = has_style(cell, "text-align", "center")
            cells.append(
                PhysicalCell(
                    row=row_index,
                    column=column,
                    row_span=row_span,
                    column_span=column_span,
                    text=visible_text(cell),
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


def normalize_table(table: ET.Element) -> dict[str, Any] | None:
    """Convert an XHTML table's spacer grid into a logical table."""
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
            visible_text(descendant)
            for descendant in table
            if local_name(descendant.tag) == "caption"
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
