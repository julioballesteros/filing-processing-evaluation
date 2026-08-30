"""Tests for logical table normalization."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from filing_processing_evaluation.normalization import (
    _normalize_table,
    _validate_table,
)


def _normalized_table(source: str) -> dict[str, Any]:
    table = _normalize_table(ET.fromstring(source))
    assert table is not None
    table["id"] = "test-table"
    _validate_table(table)
    return table


def test_logical_table_aligns_offset_currency_fragments_to_numeric_columns() -> None:
    table = _normalized_table("""<table>
        <tr><td>Year ended</td><td colspan="2"></td><td>2024</td><td colspan="3"></td><td>2023</td><td></td></tr>
        <tr><td>Revenue</td><td></td><td>$</td><td>247,442</td><td colspan="2"></td><td>$</td><td>219,790</td><td></td></tr>
        <tr><td>Net income</td><td colspan="2"></td><td>88,308</td><td colspan="3"></td><td>71,383</td><td></td></tr>
        </table>""")

    revenue = [cell for cell in table["cells"] if cell["row"] == 1]
    assert [(cell["column"], cell["text"]) for cell in revenue] == [
        (0, "Revenue"),
        (1, "$247,442"),
        (2, "$219,790"),
    ]


def test_logical_table_keeps_wide_and_row_spanning_headers_disjoint() -> None:
    wide_header = _normalized_table("""<table>
        <tr><td rowspan="2" colspan="3">(in millions)</td><td colspan="9"></td><td colspan="15">Year ended December 31,</td><td colspan="6"></td></tr>
        <tr><td colspan="15"></td><td colspan="3">2025</td><td colspan="3"></td><td colspan="3">2024</td><td colspan="3"></td><td colspan="3">2023</td></tr>
        <tr><td colspan="3">Operating activities</td><td colspan="15"></td><td>$</td><td>(147)</td><td colspan="4"></td><td>$</td><td>(42)</td><td colspan="4"></td><td>$</td><td>13</td><td></td></tr>
        </table>""")
    cells = {cell["text"]: cell for cell in wide_header["cells"]}
    assert (cells["(in millions)"]["column"], cells["(in millions)"]["row_span"]) == (
        0,
        2,
    )
    assert cells["Year ended December 31,"]["column"] == 1
    assert [cells[str(year)]["column"] for year in (2025, 2024, 2023)] == [1, 2, 3]

    tied_header = _normalized_table("""<table>
        <tr><td colspan="33"></td><td rowspan="2" colspan="3">Collateral</td><td colspan="3"></td></tr>
        <tr><td colspan="30"></td><td colspan="3">Nonperforming</td></tr>
        <tr><td colspan="3">Exposure</td><td colspan="27"></td><td>10</td><td colspan="5"></td><td>20</td><td colspan="2"></td></tr>
        <tr><td colspan="3">Other</td><td colspan="27"></td><td>11</td><td colspan="5"></td><td>21</td><td colspan="2"></td></tr>
        </table>""")
    cells = {cell["text"]: cell for cell in tied_header["cells"]}
    assert (cells["Collateral"]["column"], cells["Collateral"]["row_span"]) == (
        2,
        2,
    )
    assert cells["Nonperforming"]["column"] == 1
