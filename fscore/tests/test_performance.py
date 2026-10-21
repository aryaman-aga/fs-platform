"""Performance (02 §3.9): a 50-sheet, ~1.5-million-cell workbook — check < 60 s, fix + write + verify < 120 s."""

from __future__ import annotations

import random
import time
from pathlib import Path

import pytest
import xlsxwriter

from fscore import api
from fscore.config import EngagementConfig

ACC = '_(* #,##0.00_);_(* \\(#,##0.00\\);_(* "-"??_);_(@_)'


def big_workbook(path: Path, *, backups: int = 46, rows: int = 820, cols: int = 40) -> Path:
    """Four print sheets linked to 46 large rupee backup sheets (~1.5 M cells); totals are plain SUMs that do not cast."""
    rng = random.Random(11)
    data = [[[round(rng.uniform(1e4, 9e6), 2) for _ in range(cols)] for _ in range(rows)] for _ in range(backups)]
    wb = xlsxwriter.Workbook(str(path))
    acc = wb.add_format({"num_format": ACC})
    bold = wb.add_format({"num_format": ACC, "bold": True, "top": 1})
    note = wb.add_worksheet("Note 4-10")
    note.write(0, 0, "4. Other expenses")
    note.write(2, 2, "Year ended March 31, 2026")
    note.write(2, 3, "Year ended March 31, 2025")
    totals = [0.0, 0.0]
    for i in range(30):
        note.write(3 + i, 0, f"Expense line {i}")
        for j, col in enumerate((2, 3)):
            b = i % backups
            value = data[b][i + 1][j] / 100000
            totals[j] += value
            note.write_formula(3 + i, col, f"='Backup {b + 1}'!{chr(65 + j)}{i + 2}/100000", acc, value)
    note.write(33, 0, "Total")
    note.write_formula(33, 2, "=SUM(C4:C33)", bold, totals[0])
    note.write_formula(33, 3, "=SUM(D4:D33)", bold, totals[1])
    note.print_area(0, 0, 33, 3)
    note.select()
    bs = wb.add_worksheet("BS")
    bs.write(0, 0, "Balance Sheet as at March 31, 2026")
    bs.write(1, 0, "(All amounts in ₹ lakhs, unless otherwise stated)")
    bs.write(2, 2, "As at March 31, 2026")
    bs.write(2, 3, "As at March 31, 2025")
    bs.write(3, 0, "Other expenses")
    bs.write_formula(3, 2, "='Note 4-10'!C34", acc, totals[0])
    bs.write_formula(3, 3, "='Note 4-10'!D34", acc, totals[1])
    bs.print_area(0, 0, 3, 3)
    bs.select()
    for b in range(backups):
        ws = wb.add_worksheet(f"Backup {b + 1}")
        for r in range(rows):
            ws.write_row(r, 0, data[b][r])
    wb.close()
    return path


@pytest.mark.slow
def test_large_workbook_within_slo(tmp_path: Path) -> None:
    src = big_workbook(tmp_path / "big.xlsx")
    cfg = EngagementConfig()
    t0 = time.perf_counter()
    res = api.check_workbook(src, cfg)
    check_s = time.perf_counter() - t0
    assert check_s < 60, f"check took {check_s:.1f}s"
    assert res.run.selftest.match_rate == 1.0
    assert any(f.rule_id == "CAST-01" for f in res.findings)
    t1 = time.perf_counter()
    plan = api.propose_fixes(src, cfg, check_result=res)
    out = api.apply_fixes(src, plan, tmp_path / "big - casting corrected.xlsx", config=cfg)
    fix_s = time.perf_counter() - t1
    assert plan.edits, "the non-casting totals must be rewritten"
    assert out.ok, out.message
    assert fix_s < 120, f"fix + write + verify took {fix_s:.1f}s"
