"""Small, hand-built statement sets for the TXT / MULTI check tests (invented data).

``clean_spec()`` returns a WorkbookSpec that every TXT-01…07 / MULTI-01…05 rule
must pass without findings; tests mutate it to inject one error at a time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fscore.checks.base import CheckContext
from fscore.config import EngagementConfig
from fscore.io.xlsx_reader import Workbook
from fscore.prepare.spec import SheetSpec, WorkbookSpec
from fscore.prepare.writer import write_workbook
from fscore.registry import implementations, load_all_checks

UNIT = "(All amounts in ₹ lakhs, unless otherwise stated)"
ENTITY = "Corvus Data Services Private Limited"
CIN = "U72900KA2015PTC081234"


def sig_block(s: SheetSpec, r: int, *, partner_col: int = 1, dir_cols: tuple[int, int] = (3, 4), date: str = "May 15, 2026",
              dins: tuple[str, str] = ("01234567", "07654321"), names: tuple[str, str] = ("Asha Raman", "Vikram Sethi"),
              cin: str = CIN, place: str = "Bengaluru") -> int:
    """Write a two-director + partner signature block starting at row r; returns the next free row."""
    a, (c1, c2) = partner_col, dir_cols
    s.set(r, a, "As per our report of even date")
    s.set(r, c1, "For and on behalf of the Board of Directors of")
    s.set(r + 1, a, "For Synthetic & Co.")
    s.set(r + 1, c1, ENTITY)
    s.set(r + 2, a, "Chartered Accountants")
    s.set(r + 2, c1, f"CIN: {cin}")
    s.set(r + 3, a, "Firm Registration No.: 123456W")
    s.set(r + 5, a, "Rohit Mehra", style="sign_bold")
    s.set(r + 5, c1, names[0], style="sign_bold")
    s.set(r + 5, c2, names[1], style="sign_bold")
    s.set(r + 6, a, "Partner")
    s.set(r + 6, c1, "Director")
    s.set(r + 6, c2, "Director")
    s.set(r + 7, a, "Membership No.: 234567")
    s.set(r + 7, c1, f"DIN: {dins[0]}")
    s.set(r + 7, c2, f"DIN: {dins[1]}")
    s.set(r + 8, a, f"Place: {place}")
    s.set(r + 8, c1, f"Place: {place}")
    s.set(r + 9, a, f"Date: {date}")
    s.set(r + 9, c1, f"Date: {date}")
    return r + 10


def _head(s: SheetSpec, title: str, h1: str, h2: str, *, note_col: bool = True) -> int:
    s.set(1, 1, ENTITY, style="title")
    s.set(2, 1, title, style="subtitle")
    s.set(3, 1, UNIT, style="unit")
    s.set(5, 1, "Particulars", style="header_left")
    if note_col:
        s.set(5, 2, "Note", style="header_center")
    s.set(5, 3, h1, style="header")
    s.set(5, 4, h2, style="header")
    s.col_widths = {1: 48, 2: 8, 3: 18, 4: 18}
    return 6


def _lines(s: SheetSpec, r: int, rows: list[tuple[Any, ...]]) -> int:
    """rows: (caption, note|None, cy, py, style) — cy/py may be formulas starting with '='."""
    for cap, note, cy, py, *st in rows:
        style = st[0] if st else "amount"
        cap_style = "caption_bold" if style in ("amount_bold", "amount_total") else ("section" if cy is None else "caption")
        s.set(r, 1, cap, style=cap_style)
        if note is not None:
            s.set(r, 2, note, style="note_ref")
        for col, v in ((3, cy), (4, py)):
            if v is None:
                continue
            if isinstance(v, str) and v.startswith("="):
                s.set(r, col, formula=v[1:], style=style)
            else:
                s.set(r, col, v, style=style)
        r += 1
    return r


def clean_spec() -> WorkbookSpec:
    spec = WorkbookSpec()
    # ------------------------------------------------------------------ BS
    bs = spec.add(SheetSpec("BS", role="bs", selected=True))
    r = _head(bs, "Balance Sheet as at March 31, 2026", "As at March 31, 2026", "As at March 31, 2025")
    r = _lines(bs, r, [
        ("ASSETS", None, None, None),
        ("Property, plant and equipment", 3, "='Notes'!E14", "='Notes'!E11"),
        ("Trade receivables", 4, "='Notes'!C22", "='Notes'!D22"),
        ("Cash and cash equivalents", 5, "='Notes'!C28", "='Notes'!D28"),
    ])
    bs.set(r, 1, "Total assets", style="caption_bold")
    bs.set(r, 3, formula=f"SUM(C{r-3}:C{r-1})", style="amount_total")
    bs.set(r, 4, formula=f"SUM(D{r-3}:D{r-1})", style="amount_total")
    ta = r
    r += 2
    r = _lines(bs, r, [
        ("EQUITY AND LIABILITIES", None, None, None),
        ("Equity share capital", 6, "='Notes'!D35", "='Notes'!D34"),
        ("Other equity", 7, f"=C{ta}-C{r + 1}-C{r + 4}", f"=D{ta}-D{r + 1}-D{r + 4}"),
    ])
    bs.set(r, 1, "Total equity", style="caption_bold")
    bs.set(r, 3, formula=f"C{r-2}+C{r-1}", style="amount_bold")
    bs.set(r, 4, formula=f"D{r-2}+D{r-1}", style="amount_bold")
    te = r
    r += 1
    r = _lines(bs, r, [("Trade payables", 8, 50.25, 40.10)])
    bs.set(r, 1, "Total equity and liabilities", style="caption_bold")
    bs.set(r, 3, formula=f"C{te}+C{r-1}", style="amount_total")
    bs.set(r, 4, formula=f"D{te}+D{r-1}", style="amount_total")
    r += 2
    r = sig_block(bs, r)
    bs.print_area = (1, 1, r, 4)
    # ------------------------------------------------------------------ PL
    pl = spec.add(SheetSpec("PL", role="pl", selected=True))
    r = _head(pl, "Statement of Profit and Loss for the year ended March 31, 2026", "Year ended March 31, 2026", "Year ended March 31, 2025")
    r = _lines(pl, r, [
        ("Revenue from operations", 9, 900.50, 800.25),
        ("Other expenses", 10, "='Notes'!C47", "='Notes'!D47"),
    ])
    pl.set(r, 1, "Profit before tax", style="caption_bold")
    pl.set(r, 3, formula=f"C{r-2}-C{r-1}", style="amount_bold")
    pl.set(r, 4, formula=f"D{r-2}-D{r-1}", style="amount_bold")
    r += 1
    pl.set(r, 1, "Profit for the year", style="caption_bold")
    pl.set(r, 3, formula=f"C{r-1}", style="amount_total")
    pl.set(r, 4, formula=f"D{r-1}", style="amount_total")
    r += 2
    r = sig_block(pl, r)
    pl.print_area = (1, 1, r, 4)
    # ------------------------------------------------------------------ CF
    cf = spec.add(SheetSpec("CFS", role="cf", selected=True))
    r = _head(cf, "Cash Flow Statement for the year ended March 31, 2026", "Year ended March 31, 2026", "Year ended March 31, 2025", note_col=False)
    r = _lines(cf, r, [
        ("A. Cash flow from operating activities", None, None, None),
        ("Profit before tax", None, "=PL!C8", "=PL!D8"),
        ("(Increase)/decrease in trade receivables", None, -10.50, -5.25),
        ("Net cash generated from operating activities", None, "=SUM(C7:C8)", "=SUM(D7:D8)", "amount_bold"),
        ("B. Cash flow from financing activities", None, None, None),
        ("Proceeds from borrowings", None, 25.00, 10.00),
        ("Repayment of borrowings", None, -15.00, -12.00),
        ("Net cash from financing activities", None, "=SUM(C11:C12)", "=SUM(D11:D12)", "amount_bold"),
        ("Net increase in cash and cash equivalents", None, "=C9+C13", "=D9+D13", "amount_bold"),
    ])
    cf.print_area = (1, 1, r + 12, 4)
    r = sig_block(cf, r + 1)
    cf.print_area = (1, 1, r, 4)
    # ------------------------------------------------------------------ SOCE
    so = spec.add(SheetSpec("SOCIE", role="soce", selected=True))
    so.set(1, 1, ENTITY, style="title")
    so.set(2, 1, "Statement of Changes in Equity for the year ended March 31, 2026", style="subtitle")
    so.set(3, 1, UNIT, style="unit")
    so.set(5, 1, "Particulars", style="header_left")
    so.set(5, 2, "Equity share capital", style="header")
    so.set(5, 3, "Retained earnings", style="header")
    so.set(5, 4, "Total", style="header")
    so.col_widths = {1: 48, 2: 16, 3: 16, 4: 16}
    rows = [
        ("Balance as at April 1, 2024", 100.00, 120.50),
        ("Profit for the year", None, 30.25),
        ("Balance as at March 31, 2025", "=SUM(B6:B7)", "=SUM(C6:C7)"),
        ("Profit for the year", None, 40.75),
        ("Issue of equity shares", 50.00, None),
        ("Balance as at March 31, 2026", "=SUM(B8:B10)", "=SUM(C8:C10)"),
    ]
    for i, (cap, sc, re_) in enumerate(rows):
        rr = 6 + i
        so.set(rr, 1, cap, style="caption_bold" if cap.startswith("Balance") else "caption")
        for col, v in ((2, sc), (3, re_)):
            if v is None:
                so.set(rr, col, 0, style="amount")
            elif isinstance(v, str):
                so.set(rr, col, formula=v[1:], style="amount_bold")
            else:
                so.set(rr, col, v, style="amount")
        so.set(rr, 4, formula=f"B{rr}+C{rr}", style="amount_bold" if cap.startswith("Balance") else "amount")
    r = sig_block(so, 14)
    so.print_area = (1, 1, r, 4)
    # ------------------------------------------------------------------ Notes
    nt = spec.add(SheetSpec("Notes", role="note", selected=True))
    nt.set(1, 1, ENTITY, style="title")
    nt.set(2, 1, "Notes to the financial statements for the year ended March 31, 2026", style="subtitle")
    nt.set(3, 1, UNIT, style="unit")
    nt.col_widths = {1: 44, 2: 14, 3: 16, 4: 16, 5: 16}
    # Note 3 PPE roll-forward (columns: classes)
    nt.set(5, 1, "3. Property, plant and equipment", style="section")
    nt.set(6, 1, "Particulars", style="header_left")
    nt.set(6, 3, "Computers", style="header")
    nt.set(6, 4, "Furniture", style="header")
    nt.set(6, 5, "Total", style="header")
    ppe = [
        ("Gross carrying amount", None, None),
        ("As at April 1, 2024", 100.00, 40.00),
        ("Additions", 20.50, 5.25),
        ("Disposals", -4.00, 0.00),
        ("As at March 31, 2025", "=SUM(C8:C10)", "=SUM(D8:D10)"),
        ("Additions", 30.75, 6.50),
        ("Disposals", -7.35, -1.00),
        ("As at March 31, 2026", "=SUM(C11:C13)", "=SUM(D11:D13)"),
    ]
    for i, (cap, a, b) in enumerate(ppe):
        rr = 7 + i
        bold = cap.startswith("As at March")
        nt.set(rr, 1, cap, style="caption_bold" if bold else ("section" if a is None else "caption"))
        if a is None:
            continue
        for col, v in ((3, a), (4, b)):
            if isinstance(v, str):
                nt.set(rr, col, formula=v[1:], style="amount_bold")
            else:
                nt.set(rr, col, v, style="amount")
        nt.set(rr, 5, formula=f"C{rr}+D{rr}", style="amount_bold" if bold else "amount")
    # Note 4 Trade receivables (rows 17..22)
    nt.set(17, 1, "4. Trade receivables", style="section")
    nt.set(18, 3, "As at March 31, 2026", style="header")
    nt.set(18, 4, "As at March 31, 2025", style="header")
    nt.set(19, 1, "(a) Unsecured, considered good")
    nt.set(19, 3, 210.40, style="amount")
    nt.set(19, 4, 180.20, style="amount")
    nt.set(20, 1, "(b) Credit impaired")
    nt.set(20, 3, 12.10, style="amount")
    nt.set(20, 4, 8.00, style="amount")
    nt.set(21, 1, "Less: Allowance for expected credit loss")
    nt.set(21, 3, -12.10, style="amount")
    nt.set(21, 4, -8.00, style="amount")
    nt.set(22, 1, "Total", style="caption_bold")
    nt.set(22, 3, formula="SUM(C19:C21)", style="amount_total")
    nt.set(22, 4, formula="SUM(D19:D21)", style="amount_total")
    # Note 5 Cash (rows 24..28)
    nt.set(24, 1, "5. Cash and cash equivalents", style="section")
    nt.set(25, 3, "As at March 31, 2026", style="header")
    nt.set(25, 4, "As at March 31, 2025", style="header")
    nt.set(26, 1, "(i) Balances with banks in current accounts")
    nt.set(26, 3, 55.30, style="amount")
    nt.set(26, 4, 61.15, style="amount")
    nt.set(27, 1, "(ii) Cash on hand")
    nt.set(27, 3, 0.45, style="amount")
    nt.set(27, 4, 0.30, style="amount")
    nt.set(28, 1, "Total", style="caption_bold")
    nt.set(28, 3, formula="SUM(C26:C27)", style="amount_total")
    nt.set(28, 4, formula="SUM(D26:D27)", style="amount_total")
    # Note 6 Share capital reconciliation (rows 30..35)
    nt.set(30, 1, "6. Equity share capital", style="section")
    nt.set(31, 3, "Number of shares", style="header")
    nt.set(31, 4, "Amount", style="header")
    nt.set(32, 1, "Equity shares of ₹10 each")
    nt.set(33, 1, "As at April 1, 2024", style="caption")
    nt.set(33, 3, 1_000_000, style="shares")
    nt.set(33, 4, 100.00, style="amount")
    nt.set(34, 1, "As at March 31, 2025", style="caption")
    nt.set(34, 3, formula="C33", style="shares")
    nt.set(34, 4, formula="D33", style="amount")
    nt.set(35, 1, "As at March 31, 2026", style="caption_bold")
    nt.set(35, 3, 1_500_000, style="shares_bold")
    nt.set(35, 4, 150.00, style="amount_bold")
    # Note 7 Other equity, Note 8 Trade payables, Note 9 Revenue (narrative cross-refs)
    nt.set(37, 1, "7. Other equity", style="section")
    nt.set(38, 1, "Refer Statement of Changes in Equity.")
    nt.set(39, 1, "8. Trade payables", style="section")
    nt.set(40, 1, "Refer Balance Sheet.")
    nt.set(41, 1, "9. Revenue from operations", style="section")
    nt.set(42, 1, "Sale of services (March 31, 2025: ₹ 800.25 lakhs)")
    # Note 10 Other expenses (rows 43..47)
    nt.set(43, 1, "10. Other expenses", style="section")
    nt.set(44, 3, "Year ended March 31, 2026", style="header")
    nt.set(44, 4, "Year ended March 31, 2025", style="header")
    nt.set(45, 1, "Legal and professional fees")
    nt.set(45, 3, 600.10, style="amount")
    nt.set(45, 4, 500.05, style="amount")
    nt.set(46, 1, "Miscellaneous expenses")
    nt.set(46, 3, 1.25, style="amount")
    nt.set(46, 4, 2.40, style="amount")
    nt.set(47, 1, "Total", style="caption_bold")
    nt.set(47, 3, formula="SUM(C45:C46)", style="amount_total")
    nt.set(47, 4, formula="SUM(D45:D46)", style="amount_total")
    r = sig_block(nt, 50)
    nt.print_area = (1, 1, r, 5)
    # ------------------------------------------------------------------ TB backup
    tb = spec.add(SheetSpec("NS_TB_FT27", role="tb"))
    tb.set(4, 1, "Account", style="tb_header")
    tb.set(4, 2, "Account Name", style="tb_header")
    tb.set(4, 3, "FS Grouping", style="tb_header")
    tb.set(4, 4, "Mar-26", style="tb_header")
    tb.set(4, 5, "Mar-25", style="tb_header")
    tb_rows = [
        ("13020", "Intercompany Receivable - Corvus Technologies Inc.", "tr.good.related", 1_234_000, 1_000_000),
        ("22020", "Intercompany Payable - Corvus Holdings Private Limited", "tp.related", -3_715_000, -612_000),
        ("40020", "Support Service Charges - IC Cost Plus - Corvus Technologies Inc.", "rev.related_support", -8_500_000, -7_000_000),
        ("50000", "Salaries and Wages", "emp.salaries", 20_000_000, 18_000_000),
    ]
    for i, (code, name, key, cy, py) in enumerate(tb_rows):
        rr = 5 + i
        tb.set(rr, 1, code, style="tb_text")
        tb.set(rr, 2, name, style="tb_text")
        tb.set(rr, 3, key, style="tb_text")
        tb.set(rr, 4, cy, style="tb_amount")
        tb.set(rr, 5, py, style="tb_amount")
    return spec


def write(spec: WorkbookSpec, path: Path) -> Path:
    return write_workbook(spec, path, shared_formulas=False)


def context(path: Path, config: EngagementConfig | None = None) -> CheckContext:
    return CheckContext(Workbook.open(path), config or EngagementConfig())


def run_rules(path: Path, rule_prefixes: tuple[str, ...] = ("TXT-01", "TXT-02", "TXT-03", "TXT-04", "TXT-05", "TXT-07", "MULTI-"),
              config: EngagementConfig | None = None) -> CheckContext:
    """Run only the checks that implement the given rule IDs; returns the context with findings."""
    ctx = context(path, config)
    load_all_checks()
    for impl in implementations():
        if any(r.startswith(rule_prefixes) for r in impl.rule_ids) and all(r.startswith(rule_prefixes) for r in impl.rule_ids):
            impl.fn(ctx)
    return ctx


def summary(ctx: CheckContext) -> list[tuple[str, str | None, list[str], str]]:
    return [(f.rule_id, f.sheet, f.cells, f.explanation) for f in ctx.findings]
