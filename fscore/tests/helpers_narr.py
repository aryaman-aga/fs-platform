"""Small but complete synthetic entity for the narrative / Going Concern / RPT tests.

Everything is invented (the fictional Corvus group). The clean entity ties
everywhere; each test switches on one deliberate error through ``EntityOpts``.
Amounts are ₹ lakhs; the TB sheet holds rupees.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from fscore.config import EngagementConfig
from fscore.prepare.spec import SheetSpec, WorkbookSpec
from fscore.prepare.writer import write_workbook

HOLDING = "Corvus Holdings Private Limited"
FELLOW_US = "Corvus Technologies Inc."
FELLOW_UK = "Corvus Analytics Limited"
DIRECTOR = "Asha Raman (Director)"

GC_CLEAN = [
    "The Company has incurred a loss of ₹ 25.05 lakhs for the year ended March 31, 2026 (previous year: profit of ₹ 9.79 lakhs). "
    "The net worth of the Company as at March 31, 2026 is positive at ₹ 517.95 lakhs.",
    "Management has considered the following mitigating factors in assessing the Company's ability to continue as a going concern:",
    "(a) The holding company has infused equity of ₹ 50.00 lakhs during the year ended March 31, 2026.",
    "(b) The holding company has confirmed that it will provide continued financial support for at least twelve months from the date of approval of these financial statements.",
    "(c) Cash and cash equivalents as at March 31, 2026 were ₹ 85.15 lakhs.",
    "(d) The Company expects to generate positive cash flows from operations in the next financial year.",
    "Accordingly, these financial statements have been prepared on a going concern basis.",
]

TAX_CLEAN = "The Company has not recognised deferred tax assets on unused tax losses as there is no reasonable certainty of future taxable profits."

RPT_PARTIES = [("Holding company", HOLDING), ("Fellow subsidiary", FELLOW_US), ("Fellow subsidiary", FELLOW_UK), ("Key management personnel", DIRECTOR)]
RPT_TXN = [
    ("Support services rendered", [(FELLOW_US, 320.50, 280.10)]),
    ("Support services received", [(HOLDING, 145.20, 120.00)]),
    ("Interest on loan", [(HOLDING, 7.20, 6.80)]),
    ("Issue of equity shares", [(HOLDING, 50.00, None)]),
    ("Loan received", [(HOLDING, None, 10.00)]),
    ("Reimbursement of expenses", [(FELLOW_UK, 12.40, 9.50)]),
]
RPT_BAL = [
    ("Trade payables", [(HOLDING, 30.40, 22.10)]),
    ("Trade receivables", [(FELLOW_US, 60.30, 52.20)]),
    ("Loan payable", [(HOLDING, 91.48, 85.00)]),
    ("Recoverable from group companies", [(FELLOW_UK, 12.20, 9.10)]),
]

# TB rows: code, name, levels(3), grouping key, CY rupees (debit +), PY rupees
TB_ROWS = [
    ("11010", "Computers - Gross", "Assets", "Non Current Assets", "Property Plant and Equipment", "ppe.gross.computers", 50_000_000, 45_000_000),
    ("13000", "Trade Receivables - Domestic", "Assets", "Current Assets", "Trade Receivables", "tr.good.third_party", 16_010_000, 12_810_000),
    ("13020", f"Intercompany Receivable - {FELLOW_US}", "Assets", "Current Assets", "Intercompany Receivables", "tr.good.related", 6_030_000, 5_220_000),
    ("14000", "Bank - Current Account", "Assets", "Current Assets", "Cash and Bank", "cash.bank_current", 8_515_000, 6_025_000),
    ("15020", f"Recoverable from Group Companies - {FELLOW_UK}", "Assets", "Current Assets", "Receivables from related parties", "ofa.c.recoverable_group", 1_220_000, 910_000),
    ("20000", "Equity Share Capital - Intercompany", "Equity", "Equity", "Share Capital", "eq.share_capital", -105_000_000, -100_000_000),
    ("21010", f"Intercompany Loan - {HOLDING}", "Liabilities", "Non Current Liabilities", "Intercompany Loans", "borr.nc.loan_holding", -9_148_000, -8_500_000),
    ("22010", "Trade Payables - Others", "Liabilities", "Current Liabilities", "Trade Payables", "tp.others", -11_724_000, -6_005_000),
    ("22020", f"Intercompany Payable - {HOLDING}", "Liabilities", "Current Liabilities", "Intercompany Payables", "tp.related", -3_040_000, -2_210_000),
    ("40000", "Revenue - Sale of Services", "Income", "Revenue from Operations", "Sale of Services", "rev.services_domestic", -92_950_000, -81_990_000),
    ("40020", f"Support Service Charges - IC Cost Plus - {FELLOW_US}", "Income", "Revenue from Operations", "Intercompany Revenue", "rev.related_support", -32_050_000, -28_010_000),
    ("51010", f"Interest on Intercompany Loan - {HOLDING}", "Expenses", "Finance Costs", "Intercompany Interest", "fin.interest_holding", 720_000, 680_000),
    ("53060", f"IC Support Services - {HOLDING}", "Expenses", "Other Expenses", "Intercompany Charges", "oth.support_services_group", 14_520_000, 12_000_000),
]


@dataclass
class EntityOpts:
    gc: list[str] = field(default_factory=lambda: list(GC_CLEAN))
    gc_hidden: set[int] = field(default_factory=set)  # indexes into ``gc`` whose rows are hidden
    gc_formulas: dict[int, str] = field(default_factory=dict)  # index -> formula (formula-driven narrative)
    tax_text: str = TAX_CLEAN
    extra_narrative: list[str] = field(default_factory=list)  # paragraphs added under the contingencies note
    parties: list[tuple[str, str]] = field(default_factory=lambda: list(RPT_PARTIES))
    txn: list[tuple[str, list[tuple[str, float | None, float | None]]]] = field(default_factory=lambda: [(n, list(r)) for n, r in RPT_TXN])
    bal: list[tuple[str, list[tuple[str, float | None, float | None]]]] = field(default_factory=lambda: [(n, list(r)) for n, r in RPT_BAL])
    rpt_hidden_rows: list[tuple[str, str, float, float]] = field(default_factory=list)  # (section, party, cy, py) appended hidden
    rpt_extra_column: str | None = None  # header of an out-of-scope column in the RPT note
    rpt_extra_caption: str | None = None
    tb_rows: list[tuple] = field(default_factory=lambda: list(TB_ROWS))
    shareholder: tuple[str, float] = (HOLDING, 0.9999)
    pl_cy: dict[str, float] = field(default_factory=dict)  # overrides: revenue, other_income, expenses, current_tax, deferred_tax, oci
    soce_issue: float = 50.00


def build_entity(path: Path, opts: EntityOpts | None = None) -> Path:
    o = opts or EntityOpts()
    spec = WorkbookSpec()
    # ------------------------------------------------------------------ BS
    bs = spec.add(SheetSpec("BS", role="bs", selected=True))
    bs.set(1, 1, "Corvus Data Services Private Limited", style="title")
    bs.set(2, 1, "Balance Sheet as at March 31, 2026", style="subtitle")
    bs.set(3, 1, "(All amounts in ₹ lakhs, unless otherwise stated)", style="unit")
    bs.set(5, 1, "Particulars", style="header_left")
    bs.set(5, 2, "Note", style="header_center")
    bs.set(5, 3, "As at March 31, 2026", style="header")
    bs.set(5, 4, "As at March 31, 2025", style="header")
    lines = [
        (6, "ASSETS", None, None, None), (7, "Non-current assets", None, None, None),
        (8, "Property, plant and equipment", 3, 500.00, 450.00),
        (10, "Current assets", None, None, None),
        (11, "Trade receivables", 10, 220.40, 180.30), (12, "Cash and cash equivalents", 11, 85.15, 60.25),
        (13, "Other financial assets", 7, 12.20, 9.10),
        (17, "EQUITY AND LIABILITIES", None, None, None),
        (18, "Equity share capital", 12, 1050.00, 1000.00), (19, "Other equity", 13, -532.05, -505.50),
        (21, "Non-current liabilities", None, None, None), (22, "Borrowings", 14, 91.48, 85.00),
        (24, "Current liabilities", None, None, None), (25, "Trade payables", 17, 147.64, 82.15),
        (26, "Other current liabilities", 19, 60.68, 38.00),
    ]
    for r, cap, note, cy, py in lines:
        bs.set(r, 1, cap, style="caption" if cy is not None else "section")
        if note:
            bs.set(r, 2, note, style="note_ref")
        if cy is not None:
            bs.set(r, 3, cy, style="amount")
            bs.set(r, 4, py, style="amount")
    for r, cap, f in [(9, "Total non-current assets", "{c}8"), (14, "Total current assets", "SUM({c}11:{c}13)"), (15, "Total assets", "{c}9+{c}14"),
                      (20, "Total equity", "{c}18+{c}19"), (23, "Total non-current liabilities", "{c}22"),
                      (27, "Total current liabilities", "{c}25+{c}26"), (28, "Total equity and liabilities", "{c}20+{c}23+{c}27")]:
        bs.set(r, 1, cap, style="caption_bold")
        for c in "CD":
            bs.set(r, 3 if c == "C" else 4, formula=f.format(c=c), style="amount_bold")
    bs.print_area = (1, 1, 28, 4)
    # ------------------------------------------------------------------ PL
    pl = spec.add(SheetSpec("PL", role="pl", selected=True))
    pl.set(1, 1, "Corvus Data Services Private Limited", style="title")
    pl.set(2, 1, "Statement of Profit and Loss for the year ended March 31, 2026", style="subtitle")
    pl.set(5, 1, "Particulars", style="header_left")
    pl.set(5, 3, "Year ended March 31, 2026", style="header")
    pl.set(5, 4, "Year ended March 31, 2025", style="header")
    v = {"revenue": 1250.00, "other_income": 12.00, "expenses": 1280.30, "current_tax": 2.00, "deferred_tax": 4.75, "oci": -1.50}
    v.update(o.pl_cy)
    rows_pl = [(6, "Revenue from operations", v["revenue"], 1100.00), (7, "Other income", v["other_income"], 8.00),
               (9, "Total expenses", v["expenses"], 1094.50), (11, "Current tax", v["current_tax"], 3.40),
               (12, "Deferred tax", v["deferred_tax"], 0.31), (15, "Total other comprehensive income", v["oci"], 0.20)]
    for r, cap, cy, py in rows_pl:
        pl.set(r, 1, cap, style="caption")
        pl.set(r, 3, cy, style="amount")
        pl.set(r, 4, py, style="amount")
    for r, cap, f in [(8, "Total income", "{c}6+{c}7"), (10, "Profit/(loss) before tax", "{c}8-{c}9"), (13, "Total tax expense", "{c}11+{c}12"),
                      (14, "Profit/(loss) for the year", "{c}10-{c}13"), (16, "Total comprehensive income for the year", "{c}14+{c}15")]:
        pl.set(r, 1, cap, style="caption_bold")
        for c in "CD":
            pl.set(r, 3 if c == "C" else 4, formula=f.format(c=c), style="amount_bold")
    pl.print_area = (1, 1, 16, 4)
    # ------------------------------------------------------------------ SOCIE
    so = spec.add(SheetSpec("SOCIE", role="soce", selected=True))
    so.set(2, 1, "Statement of Changes in Equity for the year ended March 31, 2026", style="subtitle")
    so.set(5, 1, "Particulars", style="header_left")
    so.set(5, 2, "Equity share capital", style="header")
    so.set(5, 3, "Retained earnings", style="header")
    so.set(5, 4, "Total", style="header")
    so_rows = [(6, "Balance as at April 1, 2024", 1000.00, -515.49), (7, "Profit for the year", None, "PL!D14"), (8, "Other comprehensive income", None, "PL!D15"),
               (10, "Loss for the year", None, "PL!C14"), (11, "Other comprehensive income", None, "PL!C15"),
               (12, "Issue of equity shares", o.soce_issue, None)]
    for r, cap, sc, re_ in so_rows:
        so.set(r, 1, cap, style="caption")
        if sc is not None:
            so.set(r, 2, sc, style="amount")
        if isinstance(re_, str):
            so.set(r, 3, formula=re_, style="amount")
        elif re_ is not None:
            so.set(r, 3, re_, style="amount")
        so.set(r, 4, formula=f"B{r}+C{r}", style="amount")
    so.set(9, 1, "Balance as at March 31, 2025", style="caption_bold")
    so.set(13, 1, "Balance as at March 31, 2026", style="caption_bold")
    for c in "BCD":
        col = " BCD".index(c) + 1
        so.set(9, col, formula=f"SUM({c}6:{c}8)", style="amount_bold")
        so.set(13, col, formula=f"{c}9+SUM({c}10:{c}12)", style="amount_bold")
    so.print_area = (1, 1, 13, 4)
    # ------------------------------------------------------------------ share capital note
    sc = spec.add(SheetSpec("Note 12-13", role="note", selected=True))
    sc.set(1, 1, "12. Equity share capital", style="section")
    sc.set(3, 1, "Particulars", style="header_left")
    sc.set(3, 3, "As at March 31, 2026", style="header")
    sc.set(3, 4, "As at March 31, 2025", style="header")
    sc.set(4, 1, "Issued, subscribed and fully paid up: 10,500,000 (PY: 10,000,000) equity shares of ₹10 each", style="caption")
    sc.set(4, 3, 1050.00, style="amount")
    sc.set(4, 4, 1000.00, style="amount")
    sc.set(6, 1, "Details of shareholders holding more than 5% shares in the Company", style="section")
    sc.set(7, 1, "Name of shareholder", style="header_left")
    sc.set(7, 2, "Number of shares", style="header")
    sc.set(7, 3, "% holding", style="header")
    sc.set(8, 1, o.shareholder[0], style="caption")
    sc.set(8, 2, 10_499_999, style="shares")
    sc.set(8, 3, o.shareholder[1], style="pct")
    sc.print_area = (1, 1, 8, 4)
    # ------------------------------------------------------------------ other notes with related-party lines
    nt = spec.add(SheetSpec("Note 14-26", role="note", selected=True))
    r = 1

    def note_block(title: str, header: str, items: list[tuple[str, float, float]], total: bool = True) -> None:
        nonlocal r
        nt.set(r, 1, title, style="section")
        nt.set(r + 1, 3, f"{header} March 31, 2026", style="header")
        nt.set(r + 1, 4, f"{header} March 31, 2025", style="header")
        first = r + 2
        for i, (cap, cy, py) in enumerate(items):
            nt.set(first + i, 1, cap, style="caption")
            nt.set(first + i, 3, cy, style="amount")
            nt.set(first + i, 4, py, style="amount")
        last = first + len(items) - 1
        r = last + 1
        if total:
            nt.set(r, 1, "Total", style="caption_bold")
            nt.set(r, 3, formula=f"SUM(C{first}:C{last})", style="amount_bold")
            nt.set(r, 4, formula=f"SUM(D{first}:D{last})", style="amount_bold")
            r += 1
        r += 1

    note_block("14. Borrowings", "As at", [("Loan from holding company (unsecured)", 91.48, 85.00)])
    note_block("17. Trade payables", "As at", [("Payable to related parties", 30.40, 22.10), ("Others", 117.24, 60.05)])
    note_block("10. Trade receivables", "As at", [("Receivables from related parties", 60.30, 52.20), ("Others", 160.10, 128.10)])
    note_block("7. Other financial assets", "As at", [("Recoverable from group companies", 12.20, 9.10)])
    note_block("20. Revenue from operations", "Year ended", [("Support services to group companies", 320.50, 280.10), ("Sale of services - others", 929.50, 819.90)])
    note_block("25. Other expenses", "Year ended", [("Support services from group companies", 145.20, 120.00)], total=False)
    note_block("23. Finance costs", "Year ended", [("Interest on loan from holding company", 7.20, 6.80)], total=False)
    nt.set(r, 1, "26. Income tax", style="section")
    nt.set(r + 1, 1, o.tax_text, style="narrative")
    r += 2
    nt.print_area = (1, 1, r, 4)
    # ------------------------------------------------------------------ RPT note
    rp = spec.add(SheetSpec("RPT", role="rpt", selected=True))
    rp.set(1, 1, "28. Related party disclosures", style="section")
    rp.set(3, 1, "(a) Names of related parties and nature of relationship", style="section")
    rp.set(4, 1, "Nature of relationship", style="header_left")
    rp.set(4, 2, "Name of the related party", style="header_left")
    rr = 5
    for rel, name in o.parties:
        rp.set(rr, 1, rel, style="caption")
        rp.set(rr, 2, name, style="caption")
        rr += 1
    rr += 1
    hidden_rows: set[int] = set()

    def section(title: str, header: str, groups: list[tuple[str, list[tuple[str, float | None, float | None]]]], key: str) -> None:
        nonlocal rr
        rp.set(rr, 1, title, style="section")
        rp.set(rr + 1, 1, "Particulars", style="header_left")
        rp.set(rr + 1, 3, f"{header} March 31, 2026", style="header")
        rp.set(rr + 1, 4, f"{header} March 31, 2025", style="header")
        if o.rpt_extra_column:
            rp.set(rr + 1, 5, o.rpt_extra_column, style="header")
        rr += 2
        for nature, rows in groups:
            rp.set(rr, 1, nature, style="caption_bold")
            rr += 1
            for party, cy, py in rows:
                rp.set(rr, 1, party, style="caption2")
                rp.set(rr, 3, cy, style="amount") if cy is not None else rp.set(rr, 3, None, style="amount")
                rp.set(rr, 4, py, style="amount") if py is not None else rp.set(rr, 4, None, style="amount")
                if o.rpt_extra_column:
                    rp.set(rr, 5, 1.11, style="amount")
                rr += 1
        for sec, party, cy, py in o.rpt_hidden_rows:
            if sec == key:
                rp.set(rr, 1, party, style="caption2")
                rp.set(rr, 3, cy, style="amount")
                rp.set(rr, 4, py, style="amount")
                hidden_rows.add(rr)
                rr += 1
        rr += 1

    section("(b) Transactions with related parties during the year", "Year ended", o.txn, "transactions")
    section("(c) Balances outstanding at the year end", "As at", o.bal, "balances")
    if o.rpt_extra_caption:
        rp.set(rr, 1, o.rpt_extra_caption, style="caption")
        rr += 1
    rp.hidden_rows |= hidden_rows
    rp.print_area = (1, 1, rr, 5 if o.rpt_extra_column else 4)
    # ------------------------------------------------------------------ Going concern note
    gc = spec.add(SheetSpec("Note 29-34", role="note", selected=True))
    gc.set(1, 1, "29. Going concern", style="section")
    g = 2
    for i, para in enumerate(o.gc):
        if i in o.gc_formulas:
            gc.set(g, 1, formula=o.gc_formulas[i], style="narrative")
        else:
            gc.set(g, 1, para, style="narrative")
        if i in o.gc_hidden:
            gc.hidden_rows.add(g)
        g += 1
    g += 1
    gc.set(g, 1, "30. Contingent liabilities", style="section")
    gc.set(g + 1, 3, "As at March 31, 2026", style="header")
    gc.set(g + 1, 4, "As at March 31, 2025", style="header")
    gc.set(g + 2, 1, "Income tax demands under appeal", style="caption")
    gc.set(g + 2, 3, 4.50, style="amount")
    gc.set(g + 2, 4, 4.50, style="amount")
    g += 3
    for para in o.extra_narrative:
        gc.set(g, 1, para, style="narrative")
        g += 1
    gc.print_area = (1, 1, g, 4)
    # ------------------------------------------------------------------ TB (backup, not printed)
    tb = spec.add(SheetSpec("NS_TB_FT27", role="tb", selected=False))
    hdr = ["Account", "Account Name", "Level 1", "Level 2", "Level 3", "FS Grouping", "Mar-26", "Mar-25"]
    for i, h in enumerate(hdr, 1):
        tb.set(4, i, h, style="tb_header")
    for k, row in enumerate(o.tb_rows):
        rr_ = 5 + k
        for i, val in enumerate(row, 1):
            tb.set(rr_, i, val, style="tb_amount" if i >= 7 else "tb_text")
    write_workbook(spec, path)
    return path


def config(**kw: object) -> EngagementConfig:
    base: dict[str, object] = {"client": "Corvus Data Services Private Limited", "entity": "Corvus Data Services Private Limited", "units": "lakhs"}
    base.update(kw)
    return EngagementConfig.model_validate(base)


def run(path: Path, cfg: EngagementConfig | None = None):  # type: ignore[no-untyped-def]
    from fscore.runner import run_checks

    return run_checks(path, cfg or config())


def rules(result, prefix: str) -> list[str]:  # type: ignore[no-untyped-def]
    return sorted(f.rule_id for f in result.findings if f.rule_id.startswith(prefix))
