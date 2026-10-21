from __future__ import annotations

from pathlib import Path

from helpers_report import small_workbook

from fscore.config import EngagementConfig
from fscore.diff import classify_period, diff, header_end_year


def test_diff_reports_cells_moves_hidden_and_sheets(tmp_path: Path) -> None:
    a = small_workbook(tmp_path / "a.xlsx", variant="a")
    b = small_workbook(tmp_path / "b.xlsx", variant="b")
    d = diff(a, b, EngagementConfig())
    assert d.a_sha256 != d.b_sha256
    assert d.sheets_added == ["Notes"]
    assert d.sheets_removed == []
    by_cell = {(c.sheet, c.cell, c.kind): c for c in d.changes}
    assert by_cell[("BS", "C6", "value")].before == "250"
    assert by_cell[("BS", "C6", "value")].after == "250.01"
    f = by_cell[("BS", "C8", "formula")]
    assert f.before == "=SUM(C5:C7)"
    assert f.after == "=SUMPRODUCT(ROUND(C5:C7,2))"
    assert ("BS", "C9", "style") in by_cell  # number format changed
    assert "BS: row 9 hidden" in d.hidden_changes
    moves = {m.cell: m for m in d.printed_moves if m.sheet == "BS"}
    assert moves["C6"].before == "250.00"
    assert moves["C6"].after == "250.01"
    assert moves["C6"].period == "CY"
    assert not moves["C6"].is_py
    assert moves["D6"].is_py
    assert moves["D6"].period == "PY"
    assert moves["C8"].is_key
    assert moves["C8"].caption == "Total assets"
    assert moves["C9"].after == ""  # row hidden: no longer printed
    # backup sheets never produce printed moves
    assert all(m.sheet != "NS_TB_FT27" for m in d.printed_moves)


def test_identical_files_have_no_differences(tmp_path: Path) -> None:
    a = small_workbook(tmp_path / "a.xlsx")
    d = diff(a, a, EngagementConfig())
    assert not d.changes
    assert not d.printed_moves
    assert not d.hidden_changes
    assert not d.sheets_added


def test_header_year_parsing() -> None:
    cases = {
        "As at March 31, 2026": 2026, "Year ended 31 March 2025": 2025, "31.03.2026": 2026, "FY 24-25": 2025,
        "2025-26": 2026, "Qtr Mar 26": 2026, "Mar-25": 2025, "Note": None, "As at\nMarch 31, 2025": 2025,
    }
    for text, year in cases.items():
        assert header_end_year(text) == year, text
    cfg = EngagementConfig()
    assert classify_period("Three months ended March 31, 2026", cfg) == ("Q4 CY", False)
    assert classify_period("Year ended March 31, 2025", cfg) == ("PY", True)
    assert classify_period("", cfg) == (None, False)
