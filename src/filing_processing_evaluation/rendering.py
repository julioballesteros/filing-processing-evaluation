"""Safe HTML rendering for normalized filing documents."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from collections.abc import Mapping
from html import escape
from pathlib import Path
from typing import Any

from filing_processing_evaluation.dataset import DatasetError


def _text(value: Any) -> str:
    return escape(str(value), quote=True)


def _render_navigation(sections: list[Any]) -> str:
    if not sections:
        return ""
    items: list[str] = []
    for section_value in sections:
        if not isinstance(section_value, dict):
            continue
        section = section_value
        label = _text(section.get("label", "Section"))
        title = section.get("title")
        name = f"{label}: {_text(title)}" if title else label
        level = int(section.get("level", 1))
        target = _text(section.get("heading_block_id", ""))
        items.append(f'<li class="level-{level}"><a href="#{target}">{name}</a></li>')
    return (
        '<nav class="contents"><h2>Sections</h2><ol>' + "".join(items) + "</ol></nav>"
    )


def _render_table(block: Mapping[str, Any]) -> str:
    grouped: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    cells = block.get("cells", [])
    if not isinstance(cells, list):
        raise DatasetError("normalized table cells must be an array")
    for value in cells:
        if isinstance(value, dict):
            row = int(value.get("row", 0))
            grouped[row].append(value)
    rows: list[str] = []
    row_count = int(block.get("row_count", len(grouped)))
    for row_index in range(row_count):
        rendered_cells: list[str] = []
        for cell in grouped[row_index]:
            role = cell.get("role")
            tag = "th" if role in {"header", "row_header"} else "td"
            row_span = int(cell.get("row_span", 1))
            column_span = int(cell.get("column_span", 1))
            attributes = [f'data-column="{int(cell.get("column", 0))}"']
            if role == "header":
                attributes.append('scope="col"')
            elif role == "row_header":
                attributes.append('scope="row"')
            if row_span > 1:
                attributes.append(f'rowspan="{row_span}"')
            if column_span > 1:
                attributes.append(f'colspan="{column_span}"')
            cell_text = _text(cell.get("text", "")) or "&nbsp;"
            rendered_cells.append(f"<{tag} {' '.join(attributes)}>{cell_text}</{tag}>")
        rows.append("<tr>" + "".join(rendered_cells) + "</tr>")
    caption = block.get("caption")
    rendered_caption = f"<caption>{_text(caption)}</caption>" if caption else ""
    return (
        '<div class="table-scroll"><table>'
        + rendered_caption
        + "<tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def _render_blocks(blocks: list[Any]) -> str:
    rendered: list[str] = []
    current_page: int | None = None
    for value in blocks:
        if not isinstance(value, dict):
            continue
        block = value
        block_id = _text(block.get("id", ""))
        block_type = str(block.get("type", "unknown"))
        order = _text(block.get("order", ""))
        source = block.get("source", {})
        source = source if isinstance(source, dict) else {}
        page_number = int(source.get("page_number", 1))
        source_path = _text(source.get("path", ""))
        if page_number != current_page:
            rendered.append(
                f'<div class="source-page" id="source-page-{page_number}">'
                f"Source page {page_number}</div>"
            )
            current_page = page_number
        badge = (
            f'<span class="block-badge" title="{source_path}">'
            f"{order} · {_text(block_type)} · p{page_number}</span>"
        )
        if block_type == "heading":
            level = min(max(int(block.get("level", 3)) + 1, 2), 6)
            content = f"<h{level}>{_text(block.get('text', ''))}</h{level}>"
        elif block_type == "paragraph":
            content = f"<p>{_text(block.get('text', ''))}</p>"
        elif block_type == "list_item":
            content = f'<p class="list-item">{_text(block.get("text", ""))}</p>'
        elif block_type == "table":
            content = _render_table(block)
        else:
            content = (
                f"<pre>{_text(json.dumps(block, ensure_ascii=False, indent=2))}</pre>"
            )
        rendered.append(
            f'<section class="block block-{_text(block_type)}" id="{block_id}">'
            f"{badge}{content}</section>"
        )
    return "".join(rendered)


def render_normalized_html(
    document: Mapping[str, Any],
    *,
    raw_href: str | None = None,
    json_href: str | None = None,
) -> str:
    """Render one normalized document as standalone, escaped HTML."""
    metadata = document.get("document", {})
    if not isinstance(metadata, dict):
        raise DatasetError("normalized document metadata must be an object")
    sections = document.get("sections", [])
    blocks = document.get("blocks", [])
    if not isinstance(sections, list) or not isinstance(blocks, list):
        raise DatasetError("normalized document sections and blocks must be arrays")
    title = _text(metadata.get("title", document.get("filing_id", "Filing")))
    filing_id = _text(document.get("filing_id", ""))
    raw_sha256 = _text(document.get("raw_sha256", ""))
    page_count = _text(document.get("page_count", ""))
    schema_version = _text(document.get("schema_version", ""))
    links: list[str] = []
    if raw_href:
        links.append(
            f'<a href="{escape(raw_href, quote=True)}" target="_blank">Open raw filing</a>'
        )
    if json_href:
        links.append(
            f'<a href="{escape(json_href, quote=True)}" target="_blank">Open normalized JSON</a>'
        )
    toolbar = " · ".join(links)
    navigation = _render_navigation(sections)
    normalized = (
        '<section class="normalized-pane"><div class="normalized-document">'
        + navigation
        + _render_blocks(blocks)
        + "</div></section>"
    )
    raw_pane = ""
    body_class = ""
    if raw_href:
        body_class = ' class="with-raw"'
        raw_pane = (
            '<section class="raw-pane"><div class="pane-label">Raw SEC filing</div>'
            f'<iframe title="Raw SEC filing" src="{escape(raw_href, quote=True)}"></iframe>'
            "</section>"
        )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — normalized comparison</title>
  <style>
    :root {{ color-scheme: light; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; color: #17202a; background: #f3f5f7; }}
    header {{ min-height: 76px; padding: 14px 22px; background: #14213d; color: white; }}
    header h1 {{ margin: 0 0 5px; font-size: 18px; }}
    header p {{ margin: 2px 0; color: #d8e0ee; font-size: 12px; }}
    header a {{ color: #8ecae6; }}
    main {{ max-width: 1240px; margin: 0 auto; }}
    body.with-raw main {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); max-width: none; height: calc(100vh - 76px); }}
    .raw-pane, .normalized-pane {{ min-width: 0; }}
    body.with-raw .normalized-pane {{ overflow: auto; border-left: 3px solid #14213d; }}
    .raw-pane {{ position: relative; background: white; }}
    .raw-pane iframe {{ display: block; width: 100%; height: 100%; border: 0; }}
    .pane-label {{ position: absolute; z-index: 2; top: 8px; left: 8px; padding: 5px 8px; border-radius: 4px; color: white; background: rgba(20,33,61,.88); font-size: 11px; }}
    .normalized-document {{ max-width: 1000px; margin: 0 auto; padding: 26px; background: white; }}
    .contents {{ margin-bottom: 32px; padding: 18px; border: 1px solid #d7dee8; border-radius: 6px; background: #f8fafc; }}
    .contents h2 {{ margin-top: 0; font-size: 16px; }}
    .contents ol {{ margin-bottom: 0; padding-left: 24px; }}
    .contents .level-2 {{ margin-left: 18px; }}
    .contents a {{ color: #1565a7; text-decoration: none; }}
    .block {{ position: relative; margin: 16px 0; padding-top: 9px; }}
    .source-page {{ margin: 34px 0 12px; padding: 6px 9px; border-top: 2px dashed #9aa8b8; color: #667085; background: #f8fafc; font: 11px ui-monospace, monospace; }}
    .block-badge {{ position: absolute; top: -7px; right: 0; padding: 2px 5px; border-radius: 3px; color: #667085; background: #eef2f6; font: 10px ui-monospace, monospace; opacity: .25; }}
    .block:hover > .block-badge {{ opacity: 1; }}
    h2, h3, h4, h5, h6 {{ color: #14213d; line-height: 1.25; }}
    p {{ line-height: 1.55; text-align: justify; }}
    .list-item {{ padding-left: 20px; }}
    .list-item::before {{ content: "•"; margin-left: -16px; margin-right: 9px; }}
    .table-scroll {{ margin: 20px 0; overflow-x: auto; border: 1px solid #cbd5e1; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    caption {{ padding: 8px; font-weight: 700; text-align: left; }}
    td, th {{ min-width: 12px; padding: 5px 7px; border: 1px solid #d9e0e8; vertical-align: top; }}
    th {{ background: #e9f1f8; }}
    tr:nth-child(even) td {{ background: #fafbfd; }}
    pre {{ overflow: auto; padding: 12px; background: #f6f8fa; }}
    @media (max-width: 1000px) {{
      body.with-raw main {{ display: block; height: auto; }}
      .raw-pane iframe {{ height: 75vh; }}
      body.with-raw .normalized-pane {{ overflow: visible; border-left: 0; border-top: 3px solid #14213d; }}
    }}
  </style>
</head>
<body{body_class}>
  <header>
    <h1>{title}</h1>
    <p>{filing_id} · schema {schema_version} · {page_count} source pages · {len(sections)} sections · {len(blocks)} blocks</p>
    <p>Raw SHA-256: <code>{raw_sha256}</code>{(' · ' + toolbar) if toolbar else ''}</p>
  </header>
  <main>{raw_pane}{normalized}</main>
</body>
</html>
"""


def relative_href(target: Path, output: Path) -> str:
    """Return a browser-safe relative link from an output artifact."""
    return Path(os.path.relpath(target.resolve(), output.parent.resolve())).as_posix()


def write_rendered_html(html: str, path: Path) -> None:
    """Write a rendered artifact atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(html, encoding="utf-8")
    temporary.replace(path)
