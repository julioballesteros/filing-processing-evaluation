"""Semantic validation for normalized filing documents."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from filing_processing_evaluation.dataset import DatasetError
from filing_processing_evaluation.normalization.schema import SCHEMA_VERSION


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
            validate_table(block)

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


def validate_table(table: Mapping[str, Any]) -> None:
    """Validate the shape, roles, provenance, and bounds of a logical table."""
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
