"""Small synthetic workbooks for the PDF pipeline tests (invented data only)."""

from __future__ import annotations

from pathlib import Path

from fscore.prepare.spec import SheetSpec, WorkbookSpec
from fscore.prepare.writer import write_workbook

ENTITY = "Corvus Data Services Private Limited"
CIN = "U72900KA2015PTC081234"

LINES = [
    ("Property, plant and equipment", "3", 1234.57, 1100.25),
    ("Right-of-use assets", "4", 456.78, 512.4),
    ("Capital work-in-progress", "3", 0.0, 12.5),
    ("Trade receivables", "6", 1234567.89, 987654.32),
    ("Cash and cash equivalents", "7", 345.6, 289.01),
    ("Other financial assets", "8", -12.34, 7.5),
    ("Other current assets", "9", 98.76, 54.32),
]


def build_spec(
    *,
    long_rows: int = 120,
    hidden_value: float = 777.77,
    hidden_col_value: float = 4242.42,
    white_value: float | None = None,
    error_cell: bool = False,
    din_second: str = "07654321",
    bad_total_delta: float = 0.0,
) -> WorkbookSpec:
    spec = WorkbookSpec()
    bs = spec.add(SheetSpec("BS", role="bs", selected=True))
    bs.col_widths = {1: 46, 2: 7, 3: 16, 4: 16, 5: 14}
    bs.set(1, 1, ENTITY, style="title")
    bs.set(2, 1, "Balance Sheet as at March 31, 2026", style="subtitle")
    bs.set(3, 1, "(All amounts in ₹ lakhs, unless otherwise stated)", style="unit")
    bs.set(5, 1, "Particulars", style="header_left")
    bs.set(5, 2, "Note", style="header_center")
    bs.set(5, 3, "As at March 31, 2026", style="header")
    bs.set(5, 4, "As at March 31, 2025", style="header")
    bs.set(5, 5, "Rupees (helper)", style="header")
    r = 6
    for caption, note, cy, py in LINES:
        bs.set(r, 1, caption, style="caption")
        bs.set(r, 2, int(note), style="note_ref")
        bs.set(r, 3, cy, style="amount")
        bs.set(r, 4, py, style="amount")
        bs.set(r, 5, hidden_col_value + r, style="amount")
        r += 1
    # hidden row with a value that must never print
    bs.set(r, 1, "Stale row (hidden)", style="caption")
    bs.set(r, 3, hidden_value, style="amount")
    bs.set(r, 4, hidden_value, style="amount")
    bs.hidden_rows.add(r)
    r += 1
    first, last = 6, r - 1
    bs.set(r, 1, "Total assets", style="caption_bold")
    if bad_total_delta:
        bs.set(r, 3, formula=f"SUM(C{first}:C{last})+{bad_total_delta}", style="amount_total")
    else:
        bs.set(r, 3, formula=f"SUM(C{first}:C{last})", style="amount_total")
    bs.set(r, 4, formula=f"SUM(D{first}:D{last})", style="amount_total")
    total_row = r
    r += 2
    if white_value is not None:
        bs.set(r, 1, "Working (rupees)", style="white_text")
        bs.set(r, 3, white_value, style="white")
        r += 1
    if error_cell:
        bs.set(r, 1, "Broken link", style="caption")
        bs.set(r, 3, formula="#REF!+1", style="amount")
        r += 1
    r += 1
    bs.set(r, 1, "For and on behalf of the Board of Directors of", style="sign")
    bs.set(r + 1, 1, ENTITY, style="sign_bold")
    bs.set(r + 2, 1, f"CIN: {CIN}", style="sign")
    bs.set(r + 4, 1, "Asha Raman", style="sign_bold")
    bs.set(r + 4, 3, "Vikram Sethi", style="sign_bold")
    bs.set(r + 5, 1, "Director", style="sign")
    bs.set(r + 5, 3, "Director", style="sign")
    bs.set(r + 6, 1, "DIN: 01234567", style="sign")
    bs.set(r + 6, 3, f"DIN: {din_second}", style="sign")
    bs.set(r + 7, 1, "Place: Bengaluru", style="sign")
    bs.set(r + 8, 1, "Date: May 15, 2026", style="sign")
    bs.hidden_cols.add(5)
    bs.print_area = (1, 1, r + 8, 5)
    bs.meta["total_row"] = total_row
    nt = spec.add(SheetSpec("Note 3-5", role="note", selected=True))
    nt.col_widths = {1: 50, 2: 16, 3: 16}
    nt.set(1, 1, ENTITY, style="title")
    nt.set(2, 1, "Notes to the financial statements for the year ended March 31, 2026", style="subtitle")
    nt.set(4, 1, "3. Property, plant and equipment", style="section")
    nt.set(5, 2, "As at March 31, 2026", style="header")
    nt.set(5, 3, "As at March 31, 2025", style="header")
    row = 6
    for i in range(long_rows):
        nt.set(row, 1, f"Asset class {i + 1:03d}", style="caption")
        nt.set(row, 2, round(100 + i * 3.17, 2), style="amount")
        nt.set(row, 3, round(90 + i * 2.91, 2), style="amount")
        row += 1
        if i == long_rows // 2:
            nt.set(row, 1, "4. Right-of-use assets", style="section")
            row += 1
    nt.set(row, 1, "Total", style="caption_bold")
    nt.set(row, 2, formula=f"SUM(B6:B{row - 1})", style="amount_total")
    nt.set(row, 3, formula=f"SUM(C6:C{row - 1})", style="amount_total")
    nt.print_area = (1, 1, row, 3)
    tb = spec.add(SheetSpec("TB", role="tb"))
    tb.set(1, 1, "Account", style="tb_header")
    tb.set(1, 2, "Amount", style="tb_header")
    for i in range(5):
        tb.set(2 + i, 1, f"Acct {i}", style="tb_text")
        tb.set(2 + i, 2, 98765.43 + i, style="tb_amount")
    return spec


def make_workbook(path: Path, **kw: object) -> Path:
    return write_workbook(build_spec(**kw), path)  # type: ignore[arg-type]
