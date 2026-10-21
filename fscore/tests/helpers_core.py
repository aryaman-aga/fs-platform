"""Small hand-built workbooks for engine and casting tests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fscore.prepare.spec import SheetSpec, WorkbookSpec
from fscore.prepare.writer import write_workbook

Mutator = Callable[[WorkbookSpec], None]


def mini_statements(path: Path, mutate: Mutator | None = None, *, shared: bool = True) -> Path:
    """A two-statement workbook (BS + PL + a note) that is clean unless mutated."""
    spec = WorkbookSpec()
    bs = spec.add(SheetSpec("BS", role="bs", selected=True))
    bs.set(1, 1, "Corvus Data Services Private Limited", style="title")
    bs.set(2, 1, "Balance Sheet as at March 31, 2026", style="subtitle")
    bs.set(3, 1, "(All amounts in ₹ lakhs, unless otherwise stated)", style="unit")
    bs.set(5, 1, "Particulars", style="header_left")
    bs.set(5, 2, "Note", style="header_center")
    bs.set(5, 3, "As at March 31, 2026", style="header")
    bs.set(5, 4, "As at March 31, 2025", style="header")
    bs.set(6, 1, "ASSETS", style="section")
    lines = [("Property, plant and equipment", 3, 120.11, 110.05), ("Trade receivables", 4, None, None), ("Cash and cash equivalents", 5, 40.33, 30.44)]
    r = 7
    for cap, note, cy, py in lines:
        bs.set(r, 1, cap, style="caption")
        bs.set(r, 2, note, style="note_ref")
        if cy is not None:
            bs.set(r, 3, cy, style="amount")
            bs.set(r, 4, py, style="amount")
        r += 1
    # trade receivables link to the note total
    bs.set(8, 3, formula="'Note 4'!C9", style="amount")
    bs.set(8, 4, formula="'Note 4'!D9", style="amount")
    bs.set(10, 1, "Total assets", style="caption_bold")
    bs.set(10, 3, formula="SUM(C7:C9)", style="amount_total")
    bs.set(10, 4, formula="SUM(D7:D9)", style="amount_total")
    bs.set(12, 1, "EQUITY AND LIABILITIES", style="section")
    bs.set(13, 1, "Equity share capital", style="caption")
    bs.set(13, 2, 6, style="note_ref")
    bs.set(13, 3, 100.0, style="amount")
    bs.set(13, 4, 100.0, style="amount")
    bs.set(14, 1, "Other equity", style="caption")
    bs.set(14, 2, 7, style="note_ref")
    bs.set(14, 3, formula="C10-C13-C16", style="amount")
    bs.set(14, 4, formula="D10-D13-D16", style="amount")
    bs.set(15, 1, "Total equity", style="caption_bold")
    bs.set(15, 3, formula="C13+C14", style="amount_bold")
    bs.set(15, 4, formula="D13+D14", style="amount_bold")
    bs.set(16, 1, "Trade payables", style="caption")
    bs.set(16, 2, 8, style="note_ref")
    bs.set(16, 3, 50.0, style="amount")
    bs.set(16, 4, 45.0, style="amount")
    bs.set(17, 1, "Total equity and liabilities", style="caption_bold")
    bs.set(17, 3, formula="C15+C16", style="amount_total")
    bs.set(17, 4, formula="D15+D16", style="amount_total")
    bs.print_area = (1, 1, 17, 4)
    bs.col_widths = {1: 42, 2: 7, 3: 15, 4: 15}

    note = spec.add(SheetSpec("Note 4", role="note", selected=True))
    note.set(1, 1, "4. Trade receivables", style="caption_bold")
    note.set(3, 3, "As at March 31, 2026", style="header")
    note.set(3, 4, "As at March 31, 2025", style="header")
    rows = [("Unsecured, considered good", 50.114, 40.222), ("Credit impaired", 10.224, 9.333), ("Receivables from related parties", 20.334, 20.444)]
    for i, (cap, cy, py) in enumerate(rows):
        note.set(4 + i, 1, cap, style="caption")
        note.set(4 + i, 3, cy, style="amount")
        note.set(4 + i, 4, py, style="amount")
    note.set(7, 1, "Total", style="caption_bold")
    note.set(7, 3, formula="SUMPRODUCT(ROUND(C4:C6,2))", style="amount_bold")
    note.set(7, 4, formula="SUMPRODUCT(ROUND(D4:D6,2))", style="amount_bold")
    note.set(8, 1, "Less: Allowance for expected credit loss", style="caption")
    note.set(8, 3, 10.22, style="amount")
    note.set(8, 4, 9.33, style="amount")
    note.set(9, 1, "Total trade receivables", style="caption_bold")
    note.set(9, 3, formula="C7-C8", style="amount_total")
    note.set(9, 4, formula="D7-D8", style="amount_total")
    note.print_area = (1, 1, 9, 4)
    note.col_widths = {1: 42, 3: 15, 4: 15}
    if mutate is not None:
        mutate(spec)
    return write_workbook(spec, path, shared_formulas=shared)


def finding_ids(result: object, rule: str) -> list[tuple[str | None, list[str]]]:
    return [(f.sheet, f.cells) for f in result.findings if f.rule_id == rule]  # type: ignore[attr-defined]
