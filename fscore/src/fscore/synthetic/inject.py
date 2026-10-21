"""Error injectors for synthetic workbooks (02 §13 item 1, 10 §4).

Each injector reproduces one error class of 02 §2.3 or one GROUP-B pattern of
10 §2 on an otherwise clean workbook, deterministically, and returns the
findings a correct engine must report (rule ID from rules.yaml + sheet/cells).
Injectors are combinable: each touches its own cells.

Phases
* ``build`` — structural: a flag consumed by the builder (adds rows / columns);
* ``post``  — formula / value / style changes made before values are computed;
* ``text``  — wording changes made after the typed narrative has been written.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from fscore.engine.refs import index_to_col, make_addr
from fscore.prepare.context import TB_FIRST_ROW, BuildContext
from fscore.prepare.spec import CellSpec, SheetSpec, WorkbookSpec

D = Decimal


@dataclass
class ExpectedFinding:
    rule_id: str
    sheet: str
    cells: list[str]
    note: str = ""
    severity: str = "High"
    source: str = "injected"  # injected | collateral | natural
    required: bool = True  # False: a reasonable engine may or may not report it

    def key(self) -> tuple[str, str, tuple[str, ...]]:
        return (self.rule_id, self.sheet, tuple(self.cells))


@dataclass
class Injector:
    name: str
    phase: str  # build | post | text
    rules: tuple[str, ...]
    description: str
    fn: Callable[[InjectContext], list[ExpectedFinding]]
    flag: str | None = None
    layouts: tuple[str, ...] = ("annual", "spfs")


@dataclass
class InjectContext:
    cx: BuildContext
    values: dict[tuple[str, int, int], Any] = field(default_factory=dict)
    protected: list[tuple[str, str]] = field(default_factory=list)

    @property
    def spec(self) -> WorkbookSpec:
        return self.cx.spec

    @property
    def index(self) -> dict[str, Any]:
        idx: dict[str, Any] = self.cx.spec.entity["index"]
        return idx

    def loc(self, alias: str, period: str) -> tuple[str, int, int]:
        sheet, row, cols = self.index[alias]
        return sheet, row, int(cols[period])

    def addr(self, alias: str, period: str) -> str:
        _s, r, c = self.loc(alias, period)
        return make_addr(r, c)

    def sheet_of(self, alias: str) -> str:
        return str(self.index[alias][0])

    def cell(self, alias: str, period: str) -> CellSpec:
        s, r, c = self.loc(alias, period)
        return self.spec.sheet(s).cells[(r, c)]

    def caption_cell(self, alias: str) -> CellSpec:
        s, r, _c = self.index[alias]
        return self.spec.sheet(s).cells[(r, 1)]

    def sh(self, name: str) -> SheetSpec:
        return self.spec.sheet(name)

    def value(self, alias: str, period: str) -> Any:
        return self.values.get(self.loc(alias, period))

    def tb_row(self, code: str) -> int:
        tb = self.spec.sheet("NS_TB_FT27")
        for r in range(TB_FIRST_ROW, self.cx.tb_last_row + 1):
            cs = tb.cells.get((r, 1))
            if cs is not None and cs.value == code:
                return r
        raise KeyError(code)

    def find_row(self, sheet: str, pattern: str, col: int = 1) -> int:
        rx = re.compile(pattern, re.I)
        for (r, c), cs in sorted(self.sh(sheet).cells.items()):
            if c == col and isinstance(cs.value, str) and rx.search(cs.value):
                return r
        raise KeyError(f"{pattern!r} not found on {sheet}")


_REGISTRY: dict[str, Injector] = {}


def injector(name: str, phase: str, rules: tuple[str, ...], description: str, *, flag: str | None = None,
             layouts: tuple[str, ...] = ("annual", "spfs")) -> Callable[[Callable[[InjectContext], list[ExpectedFinding]]], Callable[[InjectContext], list[ExpectedFinding]]]:
    def deco(fn: Callable[[InjectContext], list[ExpectedFinding]]) -> Callable[[InjectContext], list[ExpectedFinding]]:
        _REGISTRY[name] = Injector(name, phase, rules, description, fn, flag, layouts)
        return fn

    return deco


def registry() -> dict[str, Injector]:
    return dict(_REGISTRY)


def E(rule: str, sheet: str, *cells: str, note: str = "", severity: str = "High", required: bool = True) -> ExpectedFinding:
    return ExpectedFinding(rule, sheet, list(cells), note, severity, required=required)


def _plus_const(cs: CellSpec, const: str) -> None:
    assert cs.formula is not None
    cs.formula = f"{cs.formula}{const}"


def _num(x: Any) -> Decimal:
    return D(str(x)) if isinstance(x, (int, float)) else D(0)


# =========================================================================== casting family (classes 1–7)


@injector("bs_not_tally", "post", ("BAL-01",), "Class 1: a note line typed 0.01 above the TB, so the Balance Sheet does not tally on print")
def _bs_not_tally(ic: InjectContext) -> list[ExpectedFinding]:
    s, r, c = ic.loc("n611.oa.nc.capadv", "CY")
    cs = ic.sh(s).cells[(r, c)]
    cs.value = float(_num(ic.values.get((s, r, c))).quantize(D("0.01")) + D("0.01"))
    cs.formula = None
    return [E("BAL-01", "BS", ic.addr("bs.total_assets", "CY"), ic.addr("bs.total_eq_liab", "CY"), note="total assets exceed equity and liabilities by 0.01")]


@injector("subtotal_not_cast", "post", ("CAST-01",), "Class 2: two lines carry 0.004 each, so the printed lines do not add to the printed subtotal")
def _subtotal_not_cast(ic: InjectContext) -> list[ExpectedFinding]:
    out = []
    for alias in ("note.ocl.statutory", "note.ocl.advance_customers"):
        s, r, c = ic.loc(alias, "PY")
        cs = ic.sh(s).cells[(r, c)]
        cs.value = float(_num(ic.values.get((s, r, c))).quantize(D("0.01")) + D("0.004"))
        cs.formula = None
    s, r, c = ic.loc("note.ocl.total", "PY")
    out.append(E("CAST-01", s, make_addr(r, c), note="subtotal prints 0.01 more than its printed lines"))
    return out


@injector("plug_total_minus", "post", ("PLUG-01",), "Class 3: =SUM(...)-0.01 typed plug in the P&L total expenses (CY)")
def _plug_total_minus(ic: InjectContext) -> list[ExpectedFinding]:
    cs = ic.cell("pl.total_expenses", "CY")
    _plus_const(cs, "-0.01")
    return [E("PLUG-01", "PL", ic.addr("pl.total_expenses", "CY"), note="-0.01 plug inside a total"),
            E("CAST-01", "PL", ic.addr("pl.total_expenses", "CY"), note="total prints 0.01 below its lines")]


@injector("plug_round_sum", "post", ("PLUG-01",), "Class 3: =ROUND(SUM(...),2)-0.03 plug in the other-expenses note total (PY)")
def _plug_round_sum(ic: InjectContext) -> list[ExpectedFinding]:
    cs = ic.cell("note.other_expenses.total", "PY")
    cs.formula = f"ROUND({cs.formula},2)-0.03"
    a = ic.addr("note.other_expenses.total", "PY")
    return [E("PLUG-01", "Note 20-27", a, note="-0.03 plug around ROUND(SUM())"), E("CAST-01", "Note 20-27", a)]


@injector("plug_hides_difference", "post", ("PLUG-01", "XREF-03"), "Class 4: CF depreciation typed 0.03 above the P&L, hidden by a -0.03 plug in operating cash flow")
def _plug_hides_difference(ic: InjectContext) -> list[ExpectedFinding]:
    s, r, c = ic.loc("cf.depreciation", "CY")
    dep = ic.value("pl.depreciation", "CY")
    ic.sh(s).cells[(r, c)].formula = None
    ic.sh(s).cells[(r, c)].value = float(D(str(dep)).quantize(D("0.01")) + D("0.03"))
    cs = ic.cell("cf.operating", "CY")
    _plus_const(cs, "-0.03")
    return [E("PLUG-01", "CFS", ic.addr("cf.operating", "CY"), note="-0.03 plug hides a depreciation difference"),
            E("XREF-03", "CFS", make_addr(r, c), note="CF depreciation differs from the P&L by 0.03")]


@injector("crosscast_down", "post", ("CAST-02",), "Class 5: PPE schedule total column typed 0.01 below the class columns on the additions row")
def _crosscast_down(ic: InjectContext) -> list[ExpectedFinding]:
    s, r, c = ic.loc("n35.g.add_cy", "total")
    total = sum((_num(ic.values.get((s, r, cc))) for cc in (2, 3, 4)), D(0))
    cs = ic.sh(s).cells[(r, c)]
    cs.formula = None
    cs.value = float(total.quantize(D("0.01")) - D("0.01"))
    _s2, r2, c2 = ic.loc("n35.g.close_cy", "total")
    return [E("CAST-02", s, make_addr(r, c), note="row total ≠ sum of class columns"),
            E("CAST-02", s, make_addr(r2, c2), note="downward total does not cross-cast"),
            E("TYPED-01", s, make_addr(r, c), note="typed total in the total column", severity="Medium", required=False)]


@injector("ageing_total_missing", "build", ("CAST-03", "SUB-01"), "Class 6: ageing total row omits the 2–3 years and > 3 years cells", flag="ageing_total_missing")
def _ageing_total_missing(ic: InjectContext) -> list[ExpectedFinding]:
    sheet, row = ic.cx.expected_rows["ageing_total_missing"]
    return [E("CAST-03", sheet, make_addr(row, 6), make_addr(row, 7), note="blank total cells under populated buckets", severity="Medium"),
            E("SUB-01", sheet, make_addr(row, 8), note="ageing total ≠ gross trade receivables")]


@injector("hidden_row_in_total", "build", ("HID-01",), "Class 7: maturity table total includes a hidden lease row", flag="maturity_hidden_lease_row")
def _hidden_row_in_total(ic: InjectContext) -> list[ExpectedFinding]:
    sheet, row = ic.cx.expected_rows["maturity_hidden_lease_row"]
    return [E("HID-01", sheet, ic.addr("note.lease.maturity_total", "CY"), ic.addr("note.lease.maturity_total", "PY"),
              note=f"hidden row {row} contributes to the printed total")]


# =========================================================================== roll-forward (class 8, 26)


@injector("roll_omits_movement", "post", ("ROLL-01",), "Class 8: lease-liability closing formula omits the additions line")
def _roll_omits(ic: InjectContext) -> list[ExpectedFinding]:
    s, r, c = ic.loc("note.lease.close", "CY")
    letter = index_to_col(c)
    rows = {a: ic.loc(a, "CY")[1] for a in ("note.lease.interest", "note.lease.payments")}
    _s, r_open, _c = ic.loc("n1419.lease.open", "CY")
    ic.sh(s).cells[(r, c)].formula = f"{letter}{r_open}+{letter}{rows['note.lease.interest']}+{letter}{rows['note.lease.payments']}"
    return [E("ROLL-01", s, make_addr(r, c), note="opening + movements ≠ closing (additions omitted)")]


@injector("py_formula_refs_cy", "post", ("ROLL-04",), "Class 8: previous-year closing of retained earnings reuses the current-year formula")
def _py_refs_cy(ic: InjectContext) -> list[ExpectedFinding]:
    s, r, c = ic.loc("note.other_equity.re_close", "PY")
    cy = ic.cell("note.other_equity.re_close", "CY")
    ic.sh(s).cells[(r, c)].formula = cy.formula
    return [E("ROLL-04", s, make_addr(r, c), note="PY column formula points at CY cells")]


@injector("dt_sign_convention", "post", ("ROLL-05",), "Class 26: DTA table rolls only with the OCI sign inverted")
def _dt_sign(ic: InjectContext) -> list[ExpectedFinding]:
    out = []
    for k in ("CY", "PY"):
        s, r, c = ic.loc("note.dta.oci", k)
        cs = ic.sh(s).cells[(r, c)]
        cs.formula = cs.formula[1:] if cs.formula and cs.formula.startswith("-") else f"-{cs.formula}"
        sc, rc, cc = ic.loc("note.dta.close", k)
        ic.sh(sc).cells[(rc, cc)].formula = ic.cx.tb("dta", k)
        out.append(E("ROLL-05", s, make_addr(r, c), make_addr(rc, cc), note="opening + P&L − OCI = closing", severity="Medium"))
    return out


# =========================================================================== cross-reference (classes 9–13)


@injector("ageing_vs_note", "post", ("SUB-01",), "Class 9: ageing backup bucket overstated, so the ageing total differs from the note's gross receivables")
def _ageing_vs_note(ic: InjectContext) -> list[ExpectedFinding]:
    ag = ic.sh("Ageing")
    row = ic.cx.pages["ageing"].row("tr.CY.good")
    cs = ag.cells[(row, 3)]
    cs.value = float(cs.value) + 125_000.0
    return [E("SUB-01", "Note 6-11", ic.addr("note.tr.ageing_total_cy", "total"), note="ageing total ≠ note gross")]


@injector("narrative_wrong_provision", "text", ("NARR-01", "TXT-06"), "Class 10: note text quotes an allowance amount that differs from the table")
def _narr_provision(ic: InjectContext) -> list[ExpectedFinding]:
    s, r, _c = ic.index["n611.tr.ecl_text"]
    cs = ic.sh(s).cells[(r, 1)]
    cs.value = re.sub(r"₹ ([\d,]+\.\d{2})", lambda m: "₹ " + f"{D(m.group(1).replace(',', '')) + D('1.25'):,.2f}", str(cs.value), count=1)
    return [E("NARR-01", s, make_addr(r, 1), note="narrative allowance ≠ table", severity="High"),
            E("TXT-06", s, make_addr(r, 1), note="same defect via the text-number rule", severity="Medium", required=False)]


@injector("same_figure_two_values", "post", ("XREF-03",), "Class 11: SOCE PY profit typed 0.01 above the P&L, so retained earnings differ from the other-equity note")
def _same_figure(ic: InjectContext) -> list[ExpectedFinding]:
    s, r, c = ic.loc("soce.pat_py", "re")
    pat = D(str(ic.value("pl.pat", "PY"))).quantize(D("0.01"))
    cs = ic.sh(s).cells[(r, c)]
    cs.formula = None
    cs.value = float(pat + D("0.01"))
    return [E("XREF-03", "SOCIE", make_addr(r, c), note="profit in SOCE ≠ P&L profit (PY)"),
            E("XREF-03", "SOCIE", ic.addr("soce.close_py", "re"), note="SOCE retained earnings ≠ other equity note (PY)", required=False),
            E("XREF-03", "SOCIE", ic.addr("soce.close_cy", "re"), note="SOCE retained earnings ≠ other equity note (CY)", required=False)]


@injector("cf_closing_plug", "post", ("PLUG-01", "BAL-02"), "Class 12: PY cash-flow closing cash carries a +0.01 plug, so it differs from the cash note")
def _cf_closing_plug(ic: InjectContext) -> list[ExpectedFinding]:
    cs = ic.cell("cf.closing_cash", "PY")
    _plus_const(cs, "+0.01")
    a = ic.addr("cf.closing_cash", "PY")
    return [E("PLUG-01", "CFS", a), E("BAL-02", "CFS", a, note="PY CF closing ≠ BS cash"), E("CAST-01", "CFS", a)]


@injector("cf_components_inconsistent", "post", ("XREF-03",), "Class 13: CF components show all cash in current accounts; the cash note shows bank + cash on hand")
def _cf_components(ic: InjectContext) -> list[ExpectedFinding]:
    s, r, c = ic.loc("cf.components_bank", "CY")
    ic.sh(s).cells[(r, c)].formula = ic.cell("bs.cash", "CY").formula
    s2, r2, c2 = ic.loc("cf.components_hand", "CY")
    ic.sh(s2).cells[(r2, c2)].formula = None
    ic.sh(s2).cells[(r2, c2)].value = 0.0
    return [E("XREF-03", "CFS", make_addr(r, c), make_addr(r2, c2), note="components table ≠ cash-note lines")]


# =========================================================================== share capital (14, 15)


@injector("share_count_double", "build", ("SUB-06",), "Class 14 / B-T23: closing share count SUM() double-counts an interim subtotal row", flag="share_june_subtotal")
def _share_double(ic: InjectContext) -> list[ExpectedFinding]:
    sheet, row = ic.cx.expected_rows["share_june_subtotal"]
    return [E("SUB-06", sheet, make_addr(row, 2), make_addr(row, 3), note="shares × face value ≠ share capital (double counted subtotal)"),
            E("XREF-03", sheet, make_addr(row, 3), note="reconciliation closing amount ≠ share capital", required=False),
            E("ROLL-01", sheet, make_addr(row, 2), note="opening + issued ≠ closing shares", required=False)]


@injector("shareholding_pct_basis", "post", ("SUB-07",), "Class 15: promoter % computed on a different basis from the >5 % holders table")
def _pct_basis(ic: InjectContext) -> list[ExpectedFinding]:
    s, r, c = ic.loc("note.share_capital.promoter_pct", "pct_cy")
    cs = ic.sh(s).cells[(r, c)]
    cs.formula = None
    cs.value = 0.995
    return [E("SUB-07", s, make_addr(r, c), note="same holding, different %", severity="Medium")]


# =========================================================================== employee benefits (16, 17)


@injector("actuarial_split", "post", ("SUB-03",), "Class 16: current + non-current gratuity ≠ defined benefit obligation by 0.01")
def _actuarial_split(ic: InjectContext) -> list[ExpectedFinding]:
    s, r, c = ic.loc("note.dbo.benefits_paid", "CY")
    cs = ic.sh(s).cells[(r, c)]
    cs.value = float(cs.value) - 0.01
    return [E("SUB-03", s, ic.addr("note.dbo.close", "CY"), ic.addr("note.dbo.split_total", "CY"), note="DBO ≠ current + non-current provision", severity="Medium")]


@injector("expense_vs_actuarial", "post", ("SUB-03",), "Class 17: gratuity expense in the P&L note differs from the actuarial current service cost")
def _expense_vs_actuarial(ic: InjectContext) -> list[ExpectedFinding]:
    s, r, c = ic.loc("note.dbo.service_cost", "CY")
    cs = ic.sh(s).cells[(r, c)]
    cs.value = float(cs.value) + 0.5
    s2, r2, c2 = ic.loc("note.dbo.benefits_paid", "CY")
    ic.sh(s2).cells[(r2, c2)].value = float(ic.sh(s2).cells[(r2, c2)].value) - 0.5
    return [E("SUB-03", s, ic.addr("note.dbo.expense", "CY"), note="actuarial expense ≠ employee benefits note", severity="Medium")]


# =========================================================================== related parties (18, 19, RPT-09…12)


@injector("rpt_duplicate_stale", "build", ("RPT-02",), "Class 18: the same party listed twice in a balance section with CY = PY", flag="rpt_duplicate_party")
def _rpt_dup(ic: InjectContext) -> list[ExpectedFinding]:
    sheet, row = ic.cx.expected_rows["rpt_duplicate_party"]
    return [E("RPT-02", sheet, make_addr(row, 1), note="duplicate party (Pvt. Ltd. vs Private Limited) with stale CY = PY", severity="Medium"),
            # collateral: the duplicate inflates the party's balance against the TB in the three-way reconciliation
            E("RPT-09", sheet, make_addr(row - 1, 1), make_addr(row - 1, 2), make_addr(row - 1, 3), note="balance double counted vs TB", required=False)]


@injector("rpt_hidden_row", "build", ("RPT-03",), "Class 19: hidden related-party row holding a value", flag="rpt_hidden_row")
def _rpt_hidden(ic: InjectContext) -> list[ExpectedFinding]:
    sheet, row = ic.cx.expected_rows["rpt_hidden_row"]
    return [E("RPT-03", sheet, make_addr(row, 1), note="hidden RPT row with non-zero amounts")]


@injector("rpt_interest_mismatch", "post", ("RPT-04",), "Class 19: interest to the holding company in the RPT note ≠ finance-cost note")
def _rpt_interest(ic: InjectContext) -> list[ExpectedFinding]:
    s, r, c = ic.loc("rpt.txn.interest", "CY")
    cs = ic.sh(s).cells[(r, c)]
    cs.value = float(_num(ic.values.get((s, r, c))).quantize(D("0.01")) - D("1"))
    cs.formula = None
    return [E("RPT-04", s, make_addr(r, c), note="RPT interest ≠ finance costs note"), E("RPT-09", s, make_addr(r, c), note="RPT ≠ TB"),
            # collateral: the per-party loan roll-forward uses the (wrong) interest figure
            E("RPT-05", s, *_rpt_loan_cells(ic), note="loan roll-forward off by the interest error", severity="Medium", required=False)]


def _rpt_loan_cells(ic: InjectContext) -> list[str]:
    """Cells of the RPT note's loan-payable balance rows (where a per-party roll-forward finding points)."""
    sh = ic.sh("RPT")
    out = []
    for (rr, cc), cs in sh.cells.items():
        if cc == 1 and isinstance(cs.value, str) and cs.value.lower().startswith("loan payable"):
            out += [make_addr(x, y) for (x, y) in sh.cells if x in (rr, rr + 1) and y in (1, 2, 3)]
    return out or [make_addr(1, 1)]


@injector("rpt_omitted_balance", "text", ("RPT-09",), "B-T08: payable to the holding company present in FS / TB but omitted from the RPT note")
def _rpt_omit(ic: InjectContext) -> list[ExpectedFinding]:
    s, r, _c = ic.index["rpt.bal.tp"]
    sh = ic.sh(s)
    cells = []
    for (rr, cc) in list(sh.cells):
        if rr == r:
            cells.append(make_addr(rr, cc))
            sh.cells[(rr, cc)] = CellSpec(value=None, style=sh.cells[(rr, cc)].style)
    # the finding may point at the emptied slot or at the section heading where the balance belongs
    return [E("RPT-09", s, make_addr(r - 1, 1), make_addr(r, 1), note="omitted balance: trade payable to holding company")]


@injector("rpt_wrong_counterparty", "text", ("RPT-09",), "B-T11: recoverable attributed to the wrong group company in the RPT note")
def _rpt_wrong_cp(ic: InjectContext) -> list[ExpectedFinding]:
    cs = ic.caption_cell("rpt.bal.recoverable")
    ent = ic.cx.inp.entity
    cs.value = ent.fellow_subsidiaries[0] if ent.fellow_subsidiaries else "Fellow subsidiary"
    s, r, _c = ic.index["rpt.bal.recoverable"]
    return [E("RPT-09", s, make_addr(r, 1), note="wrong counterparty (TB counterparty differs)")]


@injector("rpt_gross_vs_net", "post", ("RPT-09",), "B-T12: RPT shows support services gross while the P&L line is net of a recovery")
def _rpt_gross(ic: InjectContext) -> list[ExpectedFinding]:
    s, r, c = ic.loc("rpt.txn.support", "CY")
    cs = ic.sh(s).cells[(r, c)]
    cs.value = float(_num(ic.values.get((s, r, c))).quantize(D("0.01")) + D("9.42"))
    cs.formula = None
    return [E("RPT-09", s, make_addr(r, c), note="gross vs net mismatch with the P&L / TB")]


@injector("rpt_fy_equals_q4", "post", ("RPT-09",), "B-T09: FY column of a related-party transaction repeats the Q4 figure", layouts=("spfs",))
def _rpt_fy_q4(ic: InjectContext) -> list[ExpectedFinding]:
    s, r, c = ic.loc("rpt.txn.revenue", "CY")
    _s, _r, cq = ic.loc("rpt.txn.revenue", "Q4_CY")
    ic.sh(s).cells[(r, c)].formula = f"{index_to_col(cq)}{r}"
    return [E("RPT-09", s, make_addr(r, c), note="FY column equals Q4 column (stale copy)")]


@injector("rpt_equity_not_disclosed", "text", ("RPT-10",), "B-T13: equity infusion by the holding company missing from the RPT note")
def _rpt_equity(ic: InjectContext) -> list[ExpectedFinding]:
    s, r, _c = ic.index["rpt.txn.equity"]
    sh = ic.sh(s)
    for (rr, cc) in list(sh.cells):
        if rr == r and cc > 1:
            sh.cells[(rr, cc)] = CellSpec(value=None, style=sh.cells[(rr, cc)].style)
    return [E("RPT-10", s, make_addr(r, 1), note="SOCIE issue of shares not disclosed as a related-party transaction")]


@injector("rpt_wrong_relationship", "text", ("RPT-11",), "B-T14: relationship description wrong (and misspelt) relative to the reporting entity")
def _rpt_relationship(ic: InjectContext) -> list[ExpectedFinding]:
    parties = ic.spec.entity["rpt_parties"]
    ent = ic.cx.inp.entity
    _rel, rid = parties[ent.subsidiary]
    _s, r, _c = ic.index.get(f"rpt.{rid}", ("RPT", ic.sh("RPT").ids[rid], {}))
    ic.sh("RPT").cells[(r, 1)].value = f"Setp-down subsidiary of {ent.holding}"
    return [E("RPT-11", "RPT", make_addr(r, 1), note="subsidiary described as a step-down subsidiary of the parent", severity="Medium")]


@injector("rpt_stale_caption", "text", ("RPT-12",), "RPT-12: stale period caption in the balances section")
def _rpt_stale_caption(ic: InjectContext) -> list[ExpectedFinding]:
    _s, r, _c = ic.index.get("rpt.bal.title", ("RPT", ic.sh("RPT").ids["bal.title"], {}))
    ic.sh("RPT").cells[(r, 1)].value = "(c) Balance as at June 30, 2026"
    return [E("RPT-12", "RPT", make_addr(r, 1), note="out-of-scope date in caption", severity="Medium")]


# =========================================================================== ratios (20), disclosure (21, 22)


@injector("ratio_avg_twice", "post", ("RATIO-01",), "Class 20: return on equity divides the average by 2 twice")
def _ratio_avg(ic: InjectContext) -> list[ExpectedFinding]:
    cs = ic.cell("note.ratio.roe", "CY")
    cs.formula = re.sub(r"/2\)$", "/2/2)", cs.formula or "")
    return [E("RATIO-01", ic.sheet_of("note.ratio.roe"), ic.addr("note.ratio.roe", "CY"), note="average divided by 2 twice", severity="Medium")]


@injector("ratio_roce_pbt", "post", ("RATIO-02",), "Class 20: ROCE uses profit before tax though the caption says EBIT")
def _ratio_roce(ic: InjectContext) -> list[ExpectedFinding]:
    cs = ic.cell("note.ratio.roce", "CY")
    cs.formula = re.sub(r"^\(([^+]+)\+[^)]+\)", r"(\1)", cs.formula or "")
    return [E("RATIO-02", ic.sheet_of("note.ratio.roce"), ic.addr("note.ratio.roce", "CY"), note="numerator excludes finance costs", severity="Medium")]


@injector("ratio_no_reason", "text", ("RATIO-03",), "Class 20: > 25 % variance without a reason")
def _ratio_reason(ic: InjectContext) -> list[ExpectedFinding]:
    for key in ("npr", "roe", "current_ratio", "dscr", "nct", "debt_equity", "tr_turnover", "tp_turnover", "roce"):
        s, r, cols = ic.index[f"note.ratio.{key}"]
        cs = ic.sh(s).cells[(r, cols["reason"])]
        if isinstance(cs.value, str) and cs.value.strip() not in ("", "-"):
            cs.value = "-"
            return [E("RATIO-03", s, make_addr(r, cols["var"]), make_addr(r, cols["reason"]), note="variance > 25 % unexplained", severity="Medium")]
    return []


@injector("fi_table_incomplete", "post", ("SUB-08",), "Class 21: financial-instrument table omits non-current borrowings")
def _fi_incomplete(ic: InjectContext) -> list[ExpectedFinding]:
    cs = ic.cell("note.fi.borrowings", "CY")
    cs.formula = (cs.formula or "").split("+", 1)[1]
    return [E("SUB-08", ic.sheet_of("note.fi.borrowings"), ic.addr("note.fi.borrowings", "CY"), note="non-current borrowings omitted", severity="Medium")]


@injector("ind_as_115_basis", "post", ("SUB-05",), "Class 22: disaggregation total on a different basis under the same caption")
def _revenue_basis(ic: InjectContext) -> list[ExpectedFinding]:
    cs = ic.cell("note.revenue.overseas", "CY")
    cs.formula = ic.cx.tb("rev.services_export", "CY")
    return [E("SUB-05", ic.sheet_of("note.revenue.overseas"), ic.addr("note.revenue.disaggregation_total", "CY"), note="disaggregation total ≠ revenue note total", severity="Medium")]


# =========================================================================== identity (23, 24, 25)


def _sign_row(ic: InjectContext, sheet: str, which: str) -> tuple[int, list[int]]:
    sb = ic.spec.entity["signature_blocks"][sheet]
    return int(sb[which]), list(sb["cols"])


@injector("din_mismatch", "text", ("TXT-01",), "Class 23: director B's block on the P&L shows director A's DIN")
def _din_mismatch(ic: InjectContext) -> list[ExpectedFinding]:
    row, cols = _sign_row(ic, "PL", "ids_row")
    sh = ic.sh("PL")
    din_a = sh.cells[(row, cols[1])].value
    sh.cells[(row, cols[2])].value = din_a
    return [E("TXT-01", "PL", make_addr(row, cols[2]), note="two directors share one DIN")]


@injector("din_placeholder", "text", ("TXT-01",), "Class 23: 'DIN: XXX' and 'Date: XXXXXX' placeholders in the cash flow signature block")
def _din_placeholder(ic: InjectContext) -> list[ExpectedFinding]:
    row, cols = _sign_row(ic, "CFS", "ids_row")
    drow, _ = _sign_row(ic, "CFS", "date_row")
    sh = ic.sh("CFS")
    sh.cells[(row, cols[2])].value = "DIN: XXX"
    sh.cells[(drow, cols[1])].value = "Date: XXXXXX"
    return [E("TXT-01", "CFS", make_addr(row, cols[2]), make_addr(drow, cols[1]), note="placeholders in signature block")]


@injector("signing_date_implausible", "text", ("TXT-01",), "Class 24: SOCIE signed before the period end; BS date blank")
def _signing_date(ic: InjectContext) -> list[ExpectedFinding]:
    drow, cols = _sign_row(ic, "SOCIE", "date_row")
    ic.sh("SOCIE").cells[(drow, cols[1])].value = "Date: March 15, 2026"
    brow, bcols = _sign_row(ic, "BS", "date_row")
    ic.sh("BS").cells[(brow, bcols[1])].value = "Date:"
    return [E("TXT-01", "SOCIE", make_addr(drow, cols[1]), note="signing date before period end"),
            E("TXT-01", "BS", make_addr(brow, bcols[1]), note="blank signing date")]


@injector("caption_closing_additions", "text", ("TXT-04",), "Class 25: closing row of the CWIP roll-forward labelled 'Additions'")
def _caption_closing(ic: InjectContext) -> list[ExpectedFinding]:
    cs = ic.caption_cell("note.cwip.close_cy")
    cs.value = "Additions"
    s, r, _c = ic.index["note.cwip.close_cy"]
    return [E("TXT-04", s, make_addr(r, 1), note="closing row captioned as a movement", severity="Low")]


@injector("caption_proceeds_outflow", "text", ("TXT-04",), "Class 25: 'Proceeds' caption on an outflow")
def _caption_proceeds(ic: InjectContext) -> list[ExpectedFinding]:
    cs = ic.caption_cell("cf.purchase_intangibles")
    cs.value = "Proceeds from sale of intangible assets"
    s, r, _c = ic.index["cf.purchase_intangibles"]
    return [E("TXT-04", s, make_addr(r, 1), note="'Proceeds' caption with an outflow", severity="Low")]


@injector("caption_stale_year", "text", ("TXT-02",), "Class 25: stale year inside a current-year caption")
def _caption_stale(ic: InjectContext) -> list[ExpectedFinding]:
    s, r, _c = ic.index["n2027.taxrec.expected"]
    ic.sh(s).cells[(r, 1)].value = "Computed expected tax expense/(credit) (March 31, 2024: at 25.17%)"
    return [E("TXT-02", s, make_addr(r, 1), note="stale year in caption", severity="Medium")]


@injector("caption_duplicate_numbering", "text", ("TXT-03",), "Class 25: '(b)' used twice in the other-financial-assets note")
def _caption_dup(ic: InjectContext) -> list[ExpectedFinding]:
    r = ic.find_row("Note 6-11", r"^\(a\) Non-current$")
    ic.sh("Note 6-11").cells[(r, 1)].value = "(b) Non-current"
    return [E("TXT-03", "Note 6-11", make_addr(r, 1), note="duplicate sub-item numbering", severity="Medium")]


@injector("caption_misspelt_designation", "text", ("TXT-01",), "Class 25: misspelt designation in a signature block")
def _caption_misspelt(ic: InjectContext) -> list[ExpectedFinding]:
    row, cols = _sign_row(ic, "SOCIE", "roles_row") if "roles_row" in ic.spec.entity["signature_blocks"]["SOCIE"] else (ic.spec.entity["signature_blocks"]["SOCIE"]["names_row"] + 1, ic.spec.entity["signature_blocks"]["SOCIE"]["cols"])
    ic.sh("SOCIE").cells[(row, cols[2])].value = "Directer"
    return [E("TXT-01", "SOCIE", make_addr(row, cols[2]), note="misspelt designation", severity="Low")]


# =========================================================================== presentation (28, 29, 30)


@injector("white_font_working", "post", ("HID-02",), "Class 28: rupee working figure in white font inside the print area")
def _white(ic: InjectContext) -> list[ExpectedFinding]:
    s, r, _c = ic.loc("note.cash.total", "CY")
    rupees = float(ic.cx.inp.tb.by_key("cash.bank_current", "CY"))
    ic.sh(s).set(r, 4, rupees, style="white")
    return [E("HID-02", s, make_addr(r, 4), note="white font working figure in print area", severity="Medium")]


@injector("format_inconsistency", "post", ("TXT-07",), "Class 29: 0-dp and no-separator figures next to accounting 2-dp figures")
def _formats(ic: InjectContext) -> list[ExpectedFinding]:
    a = ic.cell("note.other_income.interest", "CY")
    a.style = "amount_0dp"
    s, r, c = ic.loc("n2027.oi.misc", "CY")
    ic.sh(s).cells[(r, c)].style = "amount_plain"
    return [E("TXT-07", s, ic.addr("note.other_income.interest", "CY"), make_addr(r, c), note="inconsistent decimals / separators", severity="Low")]


@injector("ref_error_hidden_row", "post", ("HID-02",), "Class 30: #REF! in a hidden row of the print area")
def _ref_hidden(ic: InjectContext) -> list[ExpectedFinding]:
    s, r, c = ic.loc("note.ofl.total", "CY")
    sh = ic.sh(s)
    hr = r + 1  # the blank spacer row after the OFL total
    sh.set(hr, 1, "Other payables (old)", style="caption")
    sh.set(hr, c, None, formula="#REF!", style="amount")
    sh.hidden_rows.add(hr)
    return [E("HID-02", s, make_addr(hr, c), note="#REF! in hidden row", severity="Medium")]


# =========================================================================== plugs ≥ 0.10, typed totals, P&L sanity


@injector("plug02_large_adjustments", "post", ("PLUG-02",), "B-T07: typed adjustments ≥ 0.10 inside links (kept and reported)")
def _plug02(ic: InjectContext) -> list[ExpectedFinding]:
    cs = ic.cell("cf.investments", "CY")
    _plus_const(cs, "-0.47")
    s, r, c = ic.loc("note.other_equity.sp_close", "PY")
    sp = ic.sh(s).cells[(r, c)]
    _plus_const(sp, "-0.16")
    return [E("PLUG-02", "CFS", ic.addr("cf.investments", "CY"), note="-0.47 typed adjustment", severity="Medium"),
            E("PLUG-02", s, make_addr(r, c), note="-0.16 typed adjustment", severity="Medium")]


@injector("typed_total", "post", ("TYPED-01",), "Hard-typed total in the other financial liabilities note")
def _typed_total(ic: InjectContext) -> list[ExpectedFinding]:
    s, r, c = ic.loc("note.ofl.total", "PY")
    v = ic.values.get((s, r, c))
    cs = ic.sh(s).cells[(r, c)]
    cs.formula = None
    cs.value = float(D(str(v)).quantize(D("0.01")))
    return [E("TYPED-01", s, make_addr(r, c), note="total typed as a constant")]


@injector("pat_points_at_tci", "post", ("XREF-03",), "B-T24: profit after tax row equals total comprehensive income")
def _pat_tci(ic: InjectContext) -> list[ExpectedFinding]:
    cs = ic.cell("pl.pat", "CY")
    _s, r, c = ic.loc("pl.oci", "CY")
    cs.formula = f"{cs.formula}+{make_addr(r, c)}"
    return [E("XREF-03", "PL", ic.addr("pl.pat", "CY"), note="PAT ≠ PBT − tax (includes OCI)")]


@injector("eps_cy_equals_py", "post", ("XREF-03",), "B-T24: CY EPS points at the PY figure")
def _eps(ic: InjectContext) -> list[ExpectedFinding]:
    cy = ic.cell("pl.eps_basic", "CY")
    py = ic.cell("pl.eps_basic", "PY")
    cy.formula = py.formula
    return [E("XREF-03", "PL", ic.addr("pl.eps_basic", "CY"), note="CY EPS identical to PY (points at PY)", severity="Medium")]


@injector("header_row_data", "post", (), "B-T22: a heading row holds data used by a total (fix engine must refuse to overwrite it)")
def _header_row(ic: InjectContext) -> list[ExpectedFinding]:
    s, r, c = ic.loc("note.ofa.c_total", "CY")
    sh = ic.sh(s)
    head = ic.find_row(s, r"^\(b\) Current$")
    sh.set(head, c, 0.43, style="amount")
    cs = sh.cells[(r, c)]
    cs.formula = re.sub(r"SUM\(([A-Z]+)(\d+):", lambda m: f"SUM({m.group(1)}{head}:", cs.formula or "")
    ic.protected.append((s, make_addr(head, c)))
    return []


@injector("cf_fx_gap", "post", ("BAL-02",), "B-T05 pattern: exchange difference on cash missing from the cash flow (gap above 0.20 — report, never plug)")
def _cf_fx(ic: InjectContext) -> list[ExpectedFinding]:
    tb = ic.sh("NS_TB_FT27")
    bank = ic.tb_row("14010")
    re_row = ic.tb_row("20200")
    q = D(ic.cx.div) * D("0.35")  # inside the 0.30–0.40 band the engine reads as a missing exchange difference
    tb.cells[(bank, 9)].value = float(D(str(tb.cells[(bank, 9)].value)) + q)
    tb.cells[(re_row, 9)].value = float(D(str(tb.cells[(re_row, 9)].value)) - q)
    # the preparer posted the translation difference into opening reserves: the CY opening is read from the TB
    # (rather than linked to the PY closing), so it no longer equals the PY closing
    s_, r_, cols = ic.index["note.other_equity.re_open"]
    note = ic.sh(s_)
    py_formula = note.cells[(r_, cols["PY"])].formula or ""
    note.cells[(r_, cols["CY"])].formula = py_formula.replace("$J$", "$I$")
    return [E("BAL-02", "CFS", ic.addr("cf.closing_cash", "CY"), note="CF closing ≠ BS cash by 0.35 (FX on cash) — investigate, do not plug"),
            E("ROLL-02", "Note 12-13", ic.addr("note.other_equity.re_open", "CY"), note="CY opening reserves ≠ PY closing (translation)", severity="High")]


# =========================================================================== going concern (GC-01…05)


def _gc_cell(ic: InjectContext, rid: str) -> tuple[CellSpec, str]:
    s, r, _c = ic.index[f"n2934.{rid}"]
    return ic.sh(s).cells[(r, 1)], make_addr(r, 1)


def _replace_fig(text: str, old: str, new: str) -> str:
    if old not in text:
        raise ValueError(f"{old!r} not in narrative")
    return text.replace(old, new, 1)


@injector("gc_stale_figure", "text", ("GC-01",), "B-T15: GC quotes a stale current-year profit/loss figure")
def _gc_stale(ic: InjectContext) -> list[ExpectedFinding]:
    cs, a = _gc_cell(ic, "gc.p1")
    facts = ic.spec.entity["gc_facts"]
    stale = D(facts["pat_cy"].replace(",", "")) * D("1.13")
    cs.value = _replace_fig(str(cs.value), "₹ " + facts["pat_cy"], f"₹ {stale.quantize(D('0.01')):,.2f}")
    return [E("GC-01", "Note 29-34", a, note="profit/loss figure ≠ statements")]


@injector("gc_rounding_mismatch", "text", ("GC-01",), "B-T16: net worth quoted 0.04 away from the Balance Sheet")
def _gc_round(ic: InjectContext) -> list[ExpectedFinding]:
    cs, a = _gc_cell(ic, "gc.p1")
    facts = ic.spec.entity["gc_facts"]
    v = D(facts["nw_cy"].replace(",", "")) - D("0.04")
    cs.value = _replace_fig(str(cs.value), "₹ " + facts["nw_cy"], f"₹ {v:,.2f}")
    return [E("GC-01", "Note 29-34", a, note="net worth rounding mismatch")]


@injector("gc_one_decimal", "text", ("GC-01",), "B-T15/16: working capital quoted to one decimal")
def _gc_one_dec(ic: InjectContext) -> list[ExpectedFinding]:
    cs, a = _gc_cell(ic, "gc.p2")
    facts = ic.spec.entity["gc_facts"]
    v = D(facts["wc_cy"].replace(",", "")).quantize(D("0.1"))
    cs.value = _replace_fig(str(cs.value), "₹ " + facts["wc_cy"], f"₹ {v:,.1f}")
    return [E("GC-01", "Note 29-34", a, note="one-decimal figure in a 2-dp note")]


@injector("gc_sign_wording", "text", ("GC-02",), "B-T19: previous-year loss written as a profit")
def _gc_sign(ic: InjectContext) -> list[ExpectedFinding]:
    cs, a = _gc_cell(ic, "gc.p1")
    facts = ic.spec.entity["gc_facts"]
    wrong = "profit" if facts["result_py"] == "loss" else "loss"
    cs.value = str(cs.value).replace(f"previous year: {facts['result_py']} of", f"previous year: {wrong} of", 1)
    return [E("GC-02", "Note 29-34", a, note="profit/loss wording contradicts the sign")]


@injector("gc_period_scope", "text", ("GC-03",), "B-T15/B-T20: out-of-scope period ('June 30, 2026') and malformed date ('31st June')")
def _gc_period(ic: InjectContext) -> list[ExpectedFinding]:
    cs, a = _gc_cell(ic, "gc.p2")
    cs.value = re.sub(r"^As at [A-Za-z]+ \d+, \d{4}", "As at June 30, 2026", str(cs.value))
    cs2, a2 = _gc_cell(ic, "gc.f1")
    cs2.value = str(cs2.value) + " (assessment updated on 31st June, 2026)"
    return [E("GC-03", "Note 29-34", a, note="out-of-scope date June 30, 2026"), E("GC-03", "Note 29-34", a2, note="malformed date 31st June")]


@injector("gc_group_wording", "text", ("GC-04",), "B-T16: standalone note says 'Group' instead of 'Company'")
def _gc_group(ic: InjectContext) -> list[ExpectedFinding]:
    cs, a = _gc_cell(ic, "gc.p3")
    cs.value = str(cs.value).replace("the Company's", "the Group's")
    return [E("GC-04", "Note 29-34", a, note="'Group' in a standalone note", severity="Medium")]


@injector("gc_hidden_factors", "build", ("GC-04",), "B-T17: mitigating factors (a)–(d) kept in hidden rows", flag="gc_hidden_factors")
def _gc_hidden(ic: InjectContext) -> list[ExpectedFinding]:
    sheet, rows = ic.cx.expected_rows["gc_hidden_factors"]
    return [E("GC-04", sheet, *[make_addr(r, 1) for r in rows], note="mitigating factors hidden", severity="Medium")]


@injector("gc_tax_note_contradiction", "text", ("GC-05",), "B-T17: tax note says the Company is in loss in a profit year")
def _gc_tax(ic: InjectContext) -> list[ExpectedFinding]:
    s, r, _c = ic.index["n2027.tax.text"]
    facts = ic.spec.entity["gc_facts"]
    status = "loss" if facts["result_cy"].startswith("earned") else "profit"
    ic.sh(s).cells[(r, 1)].value = f"As the Company is in {status} for the year, deferred tax assets on carried forward losses have been recognised only to the extent of deferred tax liabilities."
    return [E("GC-05", s, make_addr(r, 1), note="tax note contradicts the result for the year", severity="Medium")]


# =========================================================================== multi-period (MULTI-01…04)


@injector("stale_dec_column", "build", ("MULTI-01",), "MULTI-01: visible out-of-scope 'December 31, 2025' column on the Balance Sheet", flag="stale_dec_column", layouts=("spfs",))
def _stale_dec(ic: InjectContext) -> list[ExpectedFinding]:
    return [E("MULTI-01", "BS", "E5", note="column outside the configured period scope", severity="Medium")]


@injector("unsafe_hide_column", "post", ("MULTI-01", "MULTI-02"), "MULTI-02 / B-T21: stale column shares its letter with ageing buckets, so it must not be hidden")
def _unsafe_hide(ic: InjectContext) -> list[ExpectedFinding]:
    sh = ic.sh("Note 14-19")
    hdr = ic.find_row("Note 14-19", r"^Particulars$")
    sh.set(hdr, 6, "As at\nDecember 31, 2025", style="header")
    rows = []
    for alias in ("note.borrowings.term_loan_nc", "note.borrowings.holding_loan", "note.borrowings.nc_total"):
        _s, r, _c = ic.index[alias]
        v = ic.values.get((_s, r, 2))
        sh.set(r, 6, float((D(str(v)) * D("0.93")).quantize(D("0.01"))) if v is not None else 0.0, style="amount")
        rows.append(r)
    return [E("MULTI-01", "Note 14-19", make_addr(hdr, 6), note="out-of-scope column", severity="Medium"),
            E("MULTI-02", "Note 14-19", "F", note="column F also holds the trade-payables ageing bucket", severity="Medium")]


@injector("ppe_stale_june_row", "build", ("MULTI-03",), "MULTI-03: stale intermediate 'As at June 30, 2025' roll-forward row", flag="ppe_stale_june_row")
def _june_row(ic: InjectContext) -> list[ExpectedFinding]:
    sheet, row = ic.cx.expected_rows["ppe_stale_june_row"]
    return [E("MULTI-03", sheet, make_addr(row, 1), note="intermediate-date row in a roll-forward", severity="Medium")]


@injector("translation_gap", "post", ("MULTI-04", "BAL-01"), "MULTI-04 / B-T02: INR Balance Sheet does not tally while the FC one does (rate error)", layouts=("spfs",))
def _translation(ic: InjectContext) -> list[ExpectedFinding]:
    cs = ic.cell("bs.oca", "CY")
    cs.formula = f"({cs.formula})*1.12"
    return [E("MULTI-04", "BS", ic.addr("bs.total_assets", "CY"), note="FC balances; INR does not"),
            E("BAL-01", "BS", ic.addr("bs.total_assets", "CY"), ic.addr("bs.total_eq_liab", "CY"), note="gap > 0.20: investigate, not balanced")]


def build_flags(names: list[str]) -> set[str]:
    return {_REGISTRY[n].flag for n in names if _REGISTRY[n].phase == "build" and _REGISTRY[n].flag}  # type: ignore[misc]
