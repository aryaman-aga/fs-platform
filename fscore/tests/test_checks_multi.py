"""MULTI-01…05 on hand-built statement sets."""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from helpers_checks_t import clean_spec, context, run_rules, write

from fscore.checks.multi import mirror_check
from fscore.config import EngagementConfig
from fscore.models import FixKind, Severity
from fscore.prepare.spec import SheetSpec, WorkbookSpec


def run(tmp_path: Path, spec: WorkbookSpec, name: str = "wb.xlsx", config: EngagementConfig | None = None):
    p = write(spec, tmp_path / name)
    return run_rules(p, ("MULTI-",), config)


def rules(ctx, rule_id: str) -> list:
    return [f for f in ctx.findings if f.rule_id == rule_id]


def add_bs_column(spec: WorkbookSpec, col: int, header: str, *, values=(11.0, 22.0, 33.0), above: str | None = None, hidden: bool = False) -> None:
    bs = spec.sheet("BS")
    bs.set(5, col, header, style="header")
    if above:
        bs.set(4, col, above, style="header")
    for r, v in zip((7, 8, 9), values, strict=True):
        bs.set(r, col, v, style="amount")
    from fscore.engine.refs import index_to_col

    L = index_to_col(col)
    bs.set(10, col, formula=f"SUM({L}7:{L}9)", style="amount_total")
    bs.col_widths[col] = 18
    if hidden:
        bs.hidden_cols.add(col)
    r1, c1, r2, c2 = bs.print_area  # type: ignore[misc]
    bs.print_area = (r1, c1, r2, max(c2, col))


def test_out_of_scope_column_safe_to_hide(tmp_path: Path) -> None:
    spec = clean_spec()
    add_bs_column(spec, 5, "As at December 31, 2025")
    ctx = run(tmp_path, spec)
    m1 = rules(ctx, "MULTI-01")
    assert len(m1) == 1 and m1[0].sheet == "BS" and m1[0].cells == ["E5"]
    assert m1[0].fix_kind == FixKind.APPROVAL and "safe" in (m1[0].suggested_fix or "")
    assert not rules(ctx, "MULTI-02")


def test_out_of_scope_column_shared_with_other_tables_is_unsafe(tmp_path: Path) -> None:
    spec = clean_spec()
    nt = spec.sheet("Notes")
    nt.set(18, 5, "As at December 31, 2025", style="header")  # Note 4 gets a stale Dec-25 column in col E
    for r, v in ((19, 200.0), (20, 10.0), (21, -10.0)):
        nt.set(r, 5, v, style="amount")
    nt.set(22, 5, formula="SUM(E19:E21)", style="amount_total")
    ctx = run(tmp_path, spec)
    m2 = rules(ctx, "MULTI-02")
    assert m2 and m2[0].sheet == "Notes" and "another table" in m2[0].explanation  # col E also holds the PPE 'Total' column
    assert m2[0].cells[0] == "E"  # column-level finding: first cell is the column letter
    m1 = [f for f in rules(ctx, "MULTI-01") if f.sheet == "Notes" and "headed" in f.explanation]
    assert m1 and m1[0].fix_kind == FixKind.MANUAL


def test_signature_block_blocks_hiding(tmp_path: Path) -> None:
    spec = clean_spec()
    spec.sheet("BS").set(5, 4, "As at December 31, 2025", style="header")  # PY column relabelled out of scope; signatures live in col D
    ctx = run(tmp_path, spec)
    m2 = rules(ctx, "MULTI-02")
    assert m2 and "signature block" in m2[0].explanation


def test_functional_currency_column_printed(tmp_path: Path) -> None:
    spec = clean_spec()
    add_bs_column(spec, 5, "As at March 31, 2026", above="USD")
    ctx = run(tmp_path, spec)
    assert any("functional-currency (USD)" in f.explanation for f in rules(ctx, "MULTI-01"))


def test_heading_says_one_period_links_say_another(tmp_path: Path) -> None:
    spec = clean_spec()
    add_bs_column(spec, 6, "As at December 31, 2025", hidden=True)  # hidden Dec-25 helper column on the BS
    nt = spec.sheet("Notes")
    nt.set(18, 3, "As at June 30, 2026", style="header")  # Note 4 CY column relabelled, data linked to Dec-25
    nt.set(19, 3, formula="BS!F8", style="amount")
    nt.set(20, 3, formula="BS!F9", style="amount")
    ctx = run(tmp_path, spec)
    msgs = [(f.sheet, f.explanation) for f in rules(ctx, "MULTI-01")]
    assert any(s == "Notes" and "June 30, 2026" in e and "December 31, 2025" in e and "linked" in e for s, e in msgs)
    assert any(s == "Notes" and "no current-period (CY) column" in e for s, e in msgs)
    assert any(s == "Notes" and "outside the configured period scope" in e for s, e in msgs)


def _rollforward_spec() -> WorkbookSpec:
    spec = WorkbookSpec()
    s = spec.add(SheetSpec("Note 3-5", role="note", selected=True))
    s.set(1, 1, "Corvus Data Services Private Limited", style="title")
    s.set(2, 1, "Notes to the financial statements for the year ended March 31, 2026", style="subtitle")
    s.set(3, 1, "(All amounts in ₹ lakhs, unless otherwise stated)", style="unit")
    s.set(5, 1, "3. Property, plant and equipment", style="section")
    s.set(6, 1, "Particulars", style="header_left")
    s.set(6, 3, "Computers", style="header")
    s.set(6, 4, "Furniture", style="header")
    rows = [
        (7, "As at March 31, 2025", 100.0, 40.0),
        (8, "Additions", 20.0, 5.0),
        (9, "As at December 31, 2025", "=SUM(C7:C8)", "=SUM(D7:D8)"),
        (10, "Additions", 10.0, 2.0),
        (11, "Disposals", -5.0, 0.0),
        (12, "As at March 31, 2026", "=SUM(C9:C11)", "=SUM(D9:D11)"),
        (15, "As at March 31, 2025", 50.0, 10.0),
        (16, "Additions for the quarter ended June 30, 2025", 7.0, 1.0),
        (17, "Additions", 3.0, 1.0),
        (18, "As at March 31, 2026", "=SUM(C15:C17)", "=SUM(D15:D17)"),
    ]
    for r, cap, a, b in rows:
        s.set(r, 1, cap)
        for col, v in ((3, a), (4, b)):
            if isinstance(v, str):
                s.set(r, col, formula=v[1:], style="amount_bold")
            else:
                s.set(r, col, v, style="amount")
    s.set(14, 1, "Accumulated depreciation", style="section")
    s.set(14, 3, "Computers", style="header")
    s.set(14, 4, "Furniture", style="header")
    s.print_area = (1, 1, 20, 4)
    s.col_widths = {1: 44, 3: 14, 4: 14}
    return spec


def test_intermediate_rows(tmp_path: Path) -> None:
    ctx = run(tmp_path, _rollforward_spec())
    m3 = {f.cells[0]: f for f in rules(ctx, "MULTI-03")}
    assert "A9" in m3 and m3["A9"].fix_kind == FixKind.APPROVAL and "can be hidden" in m3["A9"].explanation
    assert "A16" in m3 and m3["A16"].fix_kind == FixKind.MANUAL and "cannot simply be hidden" in m3["A16"].explanation


def test_stale_quarter_row_without_year(tmp_path: Path) -> None:
    spec = _rollforward_spec()
    spec.sheet("Note 3-5").set(16, 1, "Additions for the quarter to June")
    ctx = run(tmp_path, spec)
    assert any(f.cells and f.cells[0] == "A16" for f in rules(ctx, "MULTI-03"))


def test_translation_gap(tmp_path: Path) -> None:
    spec = clean_spec()
    bs = spec.sheet("BS")
    assert bs.get(14, 1).value == "Other equity" and bs.get(17, 1).value == "Total equity and liabilities"
    bs.set(14, 3, 10.0, style="amount")  # INR other equity typed → INR BS no longer balances
    bs.set(4, 5, "USD", style="header")
    bs.set(5, 5, "As at March 31, 2026", style="header")
    for r, v in ((7, 1.0), (8, 2.0), (9, 3.0), (13, 1.5), (14, 4.0), (16, 0.5)):
        bs.set(r, 5, v, style="amount")
    bs.set(10, 5, formula="SUM(E7:E9)", style="amount_total")
    bs.set(15, 5, formula="E13+E14", style="amount_bold")
    bs.set(17, 5, formula="E15+E16", style="amount_total")
    bs.hidden_cols.add(5)
    ctx = run(tmp_path, spec)
    m4 = rules(ctx, "MULTI-04")
    assert m4 and m4[0].severity == Severity.HIGH and m4[0].cells == ["C10", "C17"] and "USD" in m4[0].explanation
    assert m4[0].difference is not None and m4[0].difference != 0


def _tb_entity(rows: list[tuple[str, str, str, float]]) -> WorkbookSpec:
    spec = WorkbookSpec()
    s = spec.add(SheetSpec("BS", role="bs", selected=True))
    s.set(1, 1, "Balance Sheet as at March 31, 2026")
    tb = spec.add(SheetSpec("NS_TB_FT27", role="tb"))
    for col, h in enumerate(("Account", "Account Name", "FS Grouping", "Mar-26"), 1):
        tb.set(4, col, h)
    for i, (code, name, key, amt) in enumerate(rows):
        tb.set(5 + i, 1, code)
        tb.set(5 + i, 2, name)
        tb.set(5 + i, 3, key)
        tb.set(5 + i, 4, amt, style="tb_amount")
    return spec


def test_intercompany_mirror_across_entities(tmp_path: Path) -> None:
    a = write(_tb_entity([
        ("22020", "Intercompany Payable - Corvus Holdings Private Limited", "tp.related", -3_715_000.0),
        ("40020", "Support Service Charges - IC Cost Plus - Corvus Holdings Private Limited", "rev.related_support", -8_500_000.0),
    ]), tmp_path / "a.xlsx")
    b = write(_tb_entity([
        ("13050", "Intercompany Receivable - Corvus Data Services Private Limited", "tr.good.related", 3_400_000.0),
        ("53060", "IC Support Services - Corvus Data Services Private Limited", "oth.support_services_group", 8_500_000.0),
    ]), tmp_path / "b.xlsx")
    ctxs = {"Corvus Data Services Private Limited": context(a), "Corvus Holdings Private Limited": context(b)}
    out = mirror_check(ctxs)
    assert len(out) == 1
    f = out[0]
    assert f.rule_id == "MULTI-05" and f.difference == Decimal("3.15")
    assert "payable" in f.explanation and "receivable" in f.explanation


def test_mirror_one_sided_balance(tmp_path: Path) -> None:
    a = write(_tb_entity([("22020", "Intercompany Payable - Corvus Holdings Private Limited", "tp.related", -100_000.0)]), tmp_path / "a.xlsx")
    b = write(_tb_entity([("50000", "Salaries", "emp.salaries", 1_000.0)]), tmp_path / "b.xlsx")
    out = mirror_check({"Corvus Data Services Private Limited": context(a), "Corvus Holdings Private Limited": context(b)})
    assert len(out) == 1 and "no matching receivable" in out[0].explanation


def test_stale_column_sharing_letter_with_ageing_buckets(tmp_path: Path) -> None:
    spec = clean_spec()
    nt = spec.sheet("Notes")
    # a stale Dec-25 column F next to the cash note …
    nt.set(25, 6, "As at\nDecember 31, 2025", style="header")
    for r, v in ((26, 51.10), (27, 0.40)):
        nt.set(r, 6, v, style="amount")
    nt.set(28, 6, formula="SUM(F26:F27)", style="amount_total")
    # … and, lower down the same sheet, an ageing table whose 1-2 years bucket also lives in column F
    nt.set(60, 1, "Ageing of trade receivables", style="section")
    nt.set(61, 3, "Less than 6 months", style="header")
    nt.set(61, 4, "6 months - 1 year", style="header")
    nt.set(61, 6, "1-2 years", style="header")
    nt.set(62, 1, "Undisputed - considered good")
    for col, v in ((3, 150.20), (4, 40.10), (6, 20.10)):
        nt.set(62, col, v, style="amount")
    nt.print_area = (1, 1, 65, 6)
    ctx = run(tmp_path, spec)
    m2 = rules(ctx, "MULTI-02")
    assert m2 and m2[0].cells[0] == "F" and "ageing / maturity buckets" in m2[0].explanation
    assert any(f.cells == ["F25"] for f in rules(ctx, "MULTI-01"))
