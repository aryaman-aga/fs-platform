"""Module P workbook builder: primary statements, notes and backups in house style.

``build_spec`` lays out every sheet and resolves cross-sheet references;
``finalise`` computes cached values, derives the typed figures that depend on
them (tax-reconciliation balancing item) and writes the typed narrative
(Going Concern, ratio explanations) from the *printed* figures, in 2 decimals,
as typed text — never formulas (firm preference, control C6).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from fscore.engine.display import render
from fscore.prepare import templates as T
from fscore.prepare.backups import build_ageing, build_far, build_tb
from fscore.prepare.context import BuildContext, long_date
from fscore.prepare.inputs import PrepareInputs
from fscore.prepare.layout import ColSpec, Fx, Page, q2, resolve_tokens, tok
from fscore.prepare.notes import (
    RATIOS,
    build_note_3_5,
    build_note_6_11,
    build_note_12_13,
    build_note_14_19,
    build_note_20_27,
    build_note_29_34,
    build_rpt,
    opening_key,
    pl_cols,
)
from fscore.prepare.spec import WorkbookSpec
from fscore.prepare.style import ACCOUNTING
from fscore.prepare.writer import compute_values

D = Decimal
Key = tuple[str, int, int]
SHEET_ORDER = ["BS", "PL", "SOCIE", "CFS", "Policies", "Note 3-5", "Note 6-11", "Note 12-13", "Note 14-19", "Note 20-27", "RPT", "Note 29-34", "NS_TB_FT27", "FAR", "Ageing"]
UNIT_WORD = {"lakhs": "lakhs", "millions": "million", "crores": "crores", "thousands": "thousands", "rupees": ""}


@dataclass
class BuiltWorkbook:
    spec: WorkbookSpec
    cx: BuildContext
    values: dict[Key, Any]


# --------------------------------------------------------------------------- signature blocks


def sign_block(cx: BuildContext, p: Page, c1: int, c2: int, *, statement: bool = True) -> None:
    ent = cx.inp.entity
    if statement:
        p.put(p.r, 1, "The accompanying notes form an integral part of these financial statements.", "unit")
        p.r += 2
    r = p.r
    p.sheet.ids["sign.top"] = r
    p.put(r, 1, "As per our report of even date", "sign")
    p.put(r, c1, "For and on behalf of the Board of Directors of", "sign")
    p.put(r + 1, 1, f"For {ent.auditor_firm}" if ent.auditor_firm else "", "sign_bold")
    p.put(r + 1, c1, ent.name, "sign_bold")
    p.put(r + 2, 1, "Chartered Accountants", "sign")
    p.put(r + 2, c1, f"{ent.registration_label}: {ent.cin}" if ent.cin else "", "sign")
    p.sheet.ids["sign.cin"] = r + 2
    p.put(r + 3, 1, f"Firm Registration No.: {ent.auditor_frn}" if ent.auditor_frn else "", "sign")
    p.sheet.ids["sign.frn"] = r + 3
    nr = r + 6
    partner = ent.auditor_partner
    p.put(nr, 1, partner.name if partner else "", "sign_bold")
    p.put(nr + 1, 1, partner.role if partner else "", "sign")
    p.put(nr + 2, 1, f"Membership No.: {partner.membership_no}" if partner and partner.membership_no else "", "sign")
    for d, col in zip(ent.directors[:2], (c1, c2), strict=False):
        p.put(nr, col, d.name, "sign_bold")
        p.put(nr + 1, col, d.role, "sign")
        p.put(nr + 2, col, f"DIN: {d.din}" if d.din else "", "sign")
    p.sheet.ids["sign.names"] = nr
    p.sheet.ids["sign.roles"] = nr + 1
    p.sheet.ids["sign.ids"] = nr + 2
    pr = nr + 4
    date_text = long_date(ent.signing_date)
    for col in (1, c1):
        p.put(pr, col, f"Place: {ent.place}", "sign")
        p.put(pr + 1, col, f"Date: {date_text}", "sign")
    p.sheet.ids["sign.place"] = pr
    p.sheet.ids["sign.date"] = pr + 1
    p.sheet.meta.setdefault("sign_cols", (1, c1, c2))
    p.r = pr + 2
    sb = cx.spec.entity.setdefault("signature_blocks", {})
    sb[p.name] = {"names_row": nr, "roles_row": nr + 1, "ids_row": nr + 2, "cin_row": r + 2, "frn_row": r + 3, "date_row": pr + 1, "place_row": pr, "cols": [1, c1, c2]}


# --------------------------------------------------------------------------- statements


def bs_page(cx: BuildContext) -> Page:
    cols = [ColSpec("CY", 3, f"As at\n{long_date(cx.ye)}"), ColSpec("PY", 4, f"As at\n{long_date(cx.py)}")]
    if cx.spfs and "stale_dec_column" in cx.inp.flags:
        cols.append(ColSpec("DEC", 5, "As at\nDecember 31, 2025"))
    p = Page(cx.spec, "BS", "bs", cols, code="bs", note_col=2)
    cx.pages["bs"] = p
    p.title(cx.inp.entity.name, T.fill(T.layout("bs")["title"], ye=long_date(cx.ye)), cx.unit_line)
    p.header()
    keys = [c.key for c in cols if c.key in ("CY", "PY")]

    def link(sheet: str, rid: str, by: dict[str, str] | None = None, key: str | None = None) -> dict[str, object]:
        out: dict[str, object] = {}
        for k in keys:
            if by:
                out[k] = Fx(tok(sheet, by[k], key or k))
            else:
                out[k] = Fx(tok(sheet, rid, key or k))
        return out

    def line(rid: str, caption: str, vals: dict[str, object], note: int | None = None, alias: str | None = None, cap: str = "caption") -> None:
        if cx.spfs and "stale_dec_column" in cx.inp.flags:
            vals = dict(vals)
            vals["DEC"] = Fx(f"ROUND(C{p.r}*0.92,2)")
        p.line(rid, caption, vals, note=note, cap=cap)
        p.index(rid, alias)

    def tot(rid: str, caption: str, members: list[str], alias: str, style: str = "amount_bold", signs: dict[str, int] | None = None) -> None:
        p.total(rid, caption, members, style=style, signs=signs, keys=[c.key for c in cols])
        p.index(rid, alias)

    p.section("ASSETS")
    p.section("Non-current assets")
    line("ppe", "(a) Property, plant and equipment", link("Note 3-5", "", {"CY": "n.cy", "PY": "n.py"}, "total"), 3, "bs.ppe")
    line("cwip", "(b) Capital work-in-progress", link("Note 3-5", "", {"CY": "c.close_cy", "PY": "c.close_py"}, "amt"), 3, "bs.cwip")
    line("rou", "(c) Right-of-use assets", link("Note 3-5", "", {"CY": "rou.n.cy", "PY": "rou.n.py"}, "amt"), 4, "bs.rou")
    line("intangibles", "(d) Other intangible assets", link("Note 3-5", "", {"CY": "software.n.cy", "PY": "software.n.py"}, "amt"), 5, "bs.intangibles")
    p.section("(e) Financial assets", style="caption")
    line("inv", "(i) Investments", link("Note 6-11", "inv.total"), 6, "bs.investments", cap="caption2")
    line("ofa.nc", "(ii) Other financial assets", link("Note 6-11", "ofa.nc.total"), 7, "bs.ofa_nc", cap="caption2")
    line("dta", "(f) Deferred tax assets (net)", link("Note 6-11", "dta.close"), 8, "bs.dta")
    line("onca", "(g) Other non-current assets", link("Note 6-11", "oa.nc.total"), 9, "bs.onca")
    tot("nca.total", "Total non-current assets", ["ppe", "cwip", "rou", "intangibles", "inv", "ofa.nc", "dta", "onca"], "bs.nca_total")
    p.section("Current assets")
    p.section("(a) Financial assets", style="caption")
    line("tr", "(i) Trade receivables", link("Note 6-11", "tr.net"), 10, "bs.trade_receivables", cap="caption2")
    line("cash", "(ii) Cash and cash equivalents", link("Note 6-11", "cash.total"), 11, "bs.cash", cap="caption2")
    line("ofa.c", "(iii) Other financial assets", link("Note 6-11", "ofa.c.total"), 7, "bs.ofa_c", cap="caption2")
    line("oca", "(b) Other current assets", link("Note 6-11", "oa.c.total"), 9, "bs.oca")
    tot("ca.total", "Total current assets", ["tr", "cash", "ofa.c", "oca"], "bs.ca_total")
    tot("assets.total", "TOTAL ASSETS", ["nca.total", "ca.total"], "bs.total_assets", style="amount_total", signs={"nca.total": 1, "ca.total": 1})
    p.blank()
    p.section("EQUITY AND LIABILITIES")
    p.section("Equity")
    line("sc", "(a) Equity share capital", link("Note 12-13", "sc.total"), 12, "bs.share_capital")
    line("oe", "(b) Other equity", link("Note 12-13", "oe.total"), 13, "bs.other_equity")
    tot("eq.total", "Total equity", ["sc", "oe"], "bs.total_equity")
    p.section("Liabilities")
    p.section("Non-current liabilities")
    p.section("(a) Financial liabilities", style="caption")
    line("borr.nc", "(i) Borrowings", link("Note 14-19", "borr.nc.total"), 14, "bs.borrowings_nc", cap="caption2")
    line("lease.nc", "(ii) Lease liabilities", link("Note 14-19", "lease.nc"), 15, "bs.lease_nc", cap="caption2")
    line("prov.nc", "(b) Provisions", link("Note 14-19", "prov.nc.total"), 16, "bs.provisions_nc")
    tot("ncl.total", "Total non-current liabilities", ["borr.nc", "lease.nc", "prov.nc"], "bs.ncl_total")
    p.section("Current liabilities")
    p.section("(a) Financial liabilities", style="caption")
    line("borr.c", "(i) Borrowings", link("Note 14-19", "borr.c.total"), 14, "bs.borrowings_c", cap="caption2")
    line("lease.c", "(ii) Lease liabilities", link("Note 14-19", "lease.c"), 15, "bs.lease_c", cap="caption2")
    p.section("(iii) Trade payables", style="caption2", note="17")
    line("tp.msme", "(A) total outstanding dues of micro enterprises and small enterprises", link("Note 14-19", "tp.msme"), None, "bs.tp_msme", cap="caption3")
    line("tp.others", "(B) total outstanding dues of creditors other than micro enterprises and small enterprises", link("Note 14-19", "tp.others"), None, "bs.tp_others", cap="caption3")
    line("ofl", "(iv) Other financial liabilities", link("Note 14-19", "ofl.total"), 18, "bs.ofl", cap="caption2")
    line("ocl", "(b) Other current liabilities", link("Note 14-19", "ocl.total"), 19, "bs.ocl")
    line("prov.c", "(c) Provisions", link("Note 14-19", "prov.c.total"), 16, "bs.provisions_c")
    line("ctl", "(d) Current tax liabilities (net)", link("Note 20-27", "ctl.bs"), 26, "bs.ctl")
    tot("cl.total", "Total current liabilities", ["borr.c", "lease.c", "tp.msme", "tp.others", "ofl", "ocl", "prov.c", "ctl"], "bs.cl_total")
    tot("liab.total", "Total liabilities", ["ncl.total", "cl.total"], "bs.total_liabilities", signs={"ncl.total": 1, "cl.total": 1})
    tot("el.total", "TOTAL EQUITY AND LIABILITIES", ["eq.total", "liab.total"], "bs.total_eq_liab", style="amount_total", signs={"eq.total": 1, "liab.total": 1})
    p.blank()
    if cx.spfs:
        # hidden functional-currency helper columns (MULTI-01/02/04 surface)
        fc_cols = {"CY": 8, "PY": 9}
        p.sheet.set(5, 8, f"{cx.inp.entity.functional_currency} million – {long_date(cx.ye)}", style="header")
        p.sheet.set(5, 9, f"{cx.inp.entity.functional_currency} million – {long_date(cx.py)}", style="header")
        for c in fc_cols.values():
            p.sheet.hidden_cols.add(c)
            p.sheet.col_widths[c] = 14
        cx.spec.entity["fc_cols"] = fc_cols
    sign_block(cx, p, 3, 4)
    p.finish(last_col=max(c.col for c in cols))
    return p


def pl_page(cx: BuildContext) -> Page:
    cols = [ColSpec(c.key, c.col + 1, c.header) for c in pl_cols(cx)]
    p = Page(cx.spec, "PL", "pl", cols, code="pl", note_col=2, landscape=cx.spfs)
    cx.pages["pl"] = p
    title_key = "pl_spfs" if cx.spfs else "pl"
    p.title(cx.inp.entity.name, T.fill(T.layout(title_key)["title"], ye=long_date(cx.ye)), cx.unit_line)
    p.header()
    pp = cx.pl_periods

    def link(sheet: str, rid: str) -> dict[str, object]:
        return {k: Fx(tok(sheet, rid, k)) for k in pp}

    def line(rid: str, caption: str, vals: dict[str, object], note: int | None = None, alias: str | None = None, cap: str = "caption", style: str | None = None) -> None:
        p.line(rid, caption, vals, note=note, cap=cap, style=style)
        p.index(rid, alias)

    line("rev", "I. Revenue from operations", link("Note 20-27", "rev.total"), 20, "pl.revenue")
    line("oi", "II. Other income", link("Note 20-27", "oi.total"), 21, "pl.other_income")
    p.total("ti", "III. Total income (I + II)", ["rev", "oi"])
    p.index("ti", "pl.total_income")
    p.section("IV. Expenses")
    line("emp", "Employee benefits expense", link("Note 20-27", "emp.total"), 22, "pl.employee")
    line("fin", "Finance costs", link("Note 20-27", "fin.total"), 23, "pl.finance_costs")
    line("dep", "Depreciation and amortisation expense", link("Note 20-27", "dep.total"), 24, "pl.depreciation")
    line("oth", "Other expenses", link("Note 20-27", "oth.total"), 25, "pl.other_expenses")
    p.total("te", "Total expenses (IV)", ["emp", "fin", "dep", "oth"])
    p.index("te", "pl.total_expenses")
    p.total("pbt", "V. Profit/(loss) before tax (III – IV)", ["ti", "te"], signs={"ti": 1, "te": -1})
    p.index("pbt", "pl.pbt")
    p.section("VI. Tax expense", note="26")
    line("tax.cur", "(1) Current tax", link("Note 20-27", "tax.current"), None, "pl.current_tax", cap="caption2")
    line("tax.def", "(2) Deferred tax", link("Note 20-27", "tax.deferred"), None, "pl.deferred_tax", cap="caption2")
    p.total("tax.total", "Total tax expense (VI)", ["tax.cur", "tax.def"])
    p.index("tax.total", "pl.tax_total")
    pat_members = ["pbt", "tax.total"]
    p.total("pat", "VII. Profit/(loss) for the year (V – VI)", pat_members, signs={"pbt": 1, "tax.total": -1}, style="amount_bold")
    p.index("pat", "pl.pat")
    p.section("VIII. Other comprehensive income")
    p.section("(A) (i) Items that will not be reclassified to profit or loss", style="caption")
    line("oci.rem", "– Remeasurement gains/(losses) on defined benefit plans", {k: cx.tb("oci.remeasurement", k, -1) for k in pp}, None, "pl.oci_remeasurement", cap="caption2")
    line("oci.tax", "(ii) Income tax relating to items that will not be reclassified to profit or loss", {k: cx.tb("oci.tax", k, -1) for k in pp}, None, "pl.oci_tax", cap="caption2")
    p.total("oci.total", "Total other comprehensive income for the year, net of tax (VIII)", ["oci.rem", "oci.tax"])
    p.index("oci.total", "pl.oci")
    p.total("tci", "IX. Total comprehensive income for the year (VII + VIII)", ["pat", "oci.total"], signs={"pat": 1, "oci.total": 1}, style="amount_total")
    p.index("tci", "pl.tci")
    p.section("X. Earnings/(loss) per equity share (face value of ₹10 each)", note="27")
    line("eps.basic", "Basic (₹)", link("Note 20-27", "eps.value"), None, "pl.eps_basic", cap="caption2", style="ratio")
    line("eps.diluted", "Diluted (₹)", link("Note 20-27", "eps.value"), None, "pl.eps_diluted", cap="caption2", style="ratio")
    p.blank()
    last = max(c.col for c in cols)
    sign_block(cx, p, last - 1, last)
    p.finish(last_col=last)
    return p


def soce_page(cx: BuildContext) -> Page:
    cols = [ColSpec("sc", 2, "Equity share capital"), ColSpec("sp", 3, "Securities premium"), ColSpec("re", 4, "Retained earnings"), ColSpec("total", 5, "Total equity")]
    p = Page(cx.spec, "SOCIE", "soce", cols, code="soce", caption_width=50)
    cx.pages["soce"] = p
    p.title(cx.inp.entity.name, T.fill(T.layout("soce")["title"], ye=long_date(cx.ye)), cx.unit_line)
    p.section("Equity share capital (refer note 12) and other equity (refer note 13)", style="caption")
    p.header("Particulars", note_label="")
    sh = {k: cx.inp.years[k].shares for k in ("CY", "PY")}

    def row_total() -> Fx:
        return Fx(f"SUM(B{p.r}:D{p.r})")

    p.line("open_py", f"Balance as at {long_date(cx.py_open)}", {
        "sc": cx.raw(-opening_key(cx, "eq.share_capital")), "sp": cx.raw(-opening_key(cx, "oe.securities_premium")),
        "re": cx.raw(-opening_key(cx, "oe.retained_earnings")), "total": row_total()}, cap="caption_bold")
    p.index("open_py", "soce.open_py")
    for yk in ("PY", "CY"):
        p.line(f"pat_{yk}", "Profit/(loss) for the year", {"re": Fx(tok("PL", "pat", yk)), "total": row_total()})
        p.index(f"pat_{yk}", f"soce.pat_{yk.lower()}")
        p.line(f"oci_{yk}", "Other comprehensive income for the year, net of tax", {"re": Fx(tok("PL", "oci.total", yk)), "total": row_total()})
        p.index(f"oci_{yk}", f"soce.oci_{yk.lower()}")
        p.total(f"tci_{yk}", "Total comprehensive income for the year", [f"pat_{yk}", f"oci_{yk}"])
        p.index(f"tci_{yk}", f"soce.tci_{yk.lower()}")
        issue: dict[str, object]
        if yk == "CY":
            issue = {"sc": Fx(tok("Note 12-13", "rec.issued", "amt_cy")), "sp": Fx(tok("Note 12-13", "sp.issued", "CY")), "total": row_total()}
        else:
            issue = {"sc": cx.raw(D(int(sh["PY"]["issued"]) * int(sh["PY"].get("face_value", 10)))),
                     "sp": cx.raw(D(int(sh["PY"]["issued"])) * D(sh["PY"].get("premium_per_share", 0))), "total": row_total()}
        p.line(f"issue_{yk}", "Issue of equity shares", issue)
        p.index(f"issue_{yk}", f"soce.issue_{yk.lower()}")
        opening = "open_py" if yk == "PY" else "close_PY"
        p.total(f"close_{yk}", f"Balance as at {long_date(cx.ye if yk == 'CY' else cx.py)}", [opening, f"tci_{yk}", f"issue_{yk}"],
                signs={opening: 1, f"tci_{yk}": 1, f"issue_{yk}": 1}, style="amount_total")
        p.index(f"close_{yk}", f"soce.close_{yk.lower()}")
    p.blank()
    sign_block(cx, p, 3, 4)
    p.finish(last_col=5)
    return p


def compute_cash_flow(cx: BuildContext, yk: str) -> dict[str, Decimal]:
    """Cash flow lines in presentation units (unrounded), mirroring the CY formulas."""
    tb = cx.inp.tb
    yd = cx.inp.years[yk]
    div = D(cx.div)

    def close(*keys: str) -> Decimal:
        return sum((tb.by_key(k, yk) for k in keys), D(0))

    def open_(*keys: str) -> Decimal:
        if yk == "CY":
            return sum((tb.by_key(k, "PY") for k in keys), D(0))
        return opening_key(cx, list(keys))

    def pl(*keys: str) -> Decimal:
        return sum((tb.by_key(k, yk) for k in keys), D(0))

    income = -pl("rev.services_domestic", "rev.services_export", "rev.related_support", "oi.interest_deposits", "oi.misc")
    exp_keys = [k for k in {a.key for a in tb.accounts} if k.split(".")[0] in ("emp", "fin", "dep", "oth")]
    out: dict[str, Decimal] = {}
    out["pbt"] = income - pl(*exp_keys)
    out["dep"] = pl("dep.ppe", "dep.rou", "dep.intangibles")
    out["fin"] = pl("fin.interest_bank", "fin.interest_holding", "fin.interest_lease")
    out["int_income"] = pl("oi.interest_deposits")
    out["op_before_wc"] = out["pbt"] + out["dep"] + out["fin"] + out["int_income"]
    tr = ("tr.good.third_party", "tr.good.related", "tr.impaired", "tr.allowance")
    out["wc_tr"] = open_(*tr) - close(*tr)
    ofa = ("ofa.nc.security_deposits", "ofa.c.interest_accrued", "ofa.c.unbilled", "ofa.c.recoverable_group")
    out["wc_ofa"] = open_(*ofa) - close(*ofa)
    oa = ("onca.capital_advances", "oca.prepaid", "oca.govt_balances", "oca.advances_suppliers")
    out["wc_oa"] = open_(*oa) - close(*oa)
    tp = ("tp.msme", "tp.others", "tp.related")
    out["wc_tp"] = -(close(*tp) - open_(*tp))
    ofl = ("ofl.employee_payable", "ofl.interest_accrued", "ofl.capital_creditors")
    out["wc_ofl"] = -(close(*ofl) - open_(*ofl))
    ocl = ("ocl.statutory_dues", "ocl.advance_customers")
    out["wc_ocl"] = -(close(*ocl) - open_(*ocl))
    prov = ("prov.nc.gratuity", "prov.c.gratuity", "prov.c.compensated_absences")
    out["wc_prov"] = -(close(*prov) - open_(*prov))
    out["remeasure"] = -pl("oci.remeasurement")
    out["cash_gen"] = sum((out[k] for k in ("op_before_wc", "wc_tr", "wc_ofa", "wc_oa", "wc_tp", "wc_ofl", "wc_ocl", "wc_prov", "remeasure")), D(0))
    ctl_close = -close("ctl.net")
    ctl_open = -open_("ctl.net")
    out["tax_paid"] = -(pl("tax.current") - (ctl_close - ctl_open))
    out["net_a"] = out["cash_gen"] + out["tax_paid"]
    out["ppe"] = -(sum((r.additions for r in yd.far.values()), D(0)) + yd.cwip.additions - yd.cwip.transfers)
    out["ppe_sale"] = sum((r.disposals - r.dep_disposals for r in yd.far.values()), D(0))
    out["intangibles"] = -yd.software.additions
    out["investments"] = -(close("inv.nc.subsidiary") - open_("inv.nc.subsidiary"))
    out["int_recd"] = -pl("oi.interest_deposits")
    out["net_b"] = sum((out[k] for k in ("ppe", "ppe_sale", "intangibles", "investments", "int_recd")), D(0))
    fv = int(yd.shares.get("face_value", 10))
    out["shares"] = D(int(yd.shares.get("issued", 0)) * fv) + D(int(yd.shares.get("issued", 0))) * D(yd.shares.get("premium_per_share", 0))
    out["borrow_in"] = yd.term_loan.get("drawn", D(0)) + yd.holding_loan.get("received", D(0))
    out["borrow_out"] = -(yd.term_loan.get("repaid", D(0)) + yd.holding_loan.get("repaid", D(0)))
    out["lease_principal"] = -(yd.lease.get("payments", D(0)) - pl("fin.interest_lease"))
    out["lease_interest"] = -pl("fin.interest_lease")
    out["interest_paid"] = -(pl("fin.interest_bank", "fin.interest_holding") - yd.holding_loan.get("credited", D(0)))
    out["net_c"] = sum((out[k] for k in ("shares", "borrow_in", "borrow_out", "lease_principal", "lease_interest", "interest_paid")), D(0))
    out["net_change"] = out["net_a"] + out["net_b"] + out["net_c"]
    cash = ("cash.bank_current", "cash.on_hand")
    out["opening"] = open_(*cash)
    out["closing"] = out["opening"] + out["net_change"]
    out["closing_bs"] = close(*cash)
    for k in list(out):
        out[k] = out[k] / div
    # recompute aggregates in presentation units for exactness
    out["op_before_wc"] = out["pbt"] + out["dep"] + out["fin"] + out["int_income"]
    out["cash_gen"] = sum((out[k] for k in ("op_before_wc", "wc_tr", "wc_ofa", "wc_oa", "wc_tp", "wc_ofl", "wc_ocl", "wc_prov", "remeasure")), D(0))
    out["net_a"] = out["cash_gen"] + out["tax_paid"]
    out["net_b"] = sum((out[k] for k in ("ppe", "ppe_sale", "intangibles", "investments", "int_recd")), D(0))
    out["net_c"] = sum((out[k] for k in ("shares", "borrow_in", "borrow_out", "lease_principal", "lease_interest", "interest_paid")), D(0))
    out["net_change"] = out["net_a"] + out["net_b"] + out["net_c"]
    out["closing"] = out["opening"] + out["net_change"]
    return out


def cf_page(cx: BuildContext) -> Page:
    cols = [ColSpec("CY", 2, f"Year ended\n{long_date(cx.ye)}"), ColSpec("PY", 3, f"Year ended\n{long_date(cx.py)}")]
    p = Page(cx.spec, "CFS", "cf", cols, code="cf", caption_width=60)
    cx.pages["cf"] = p
    p.title(cx.inp.entity.name, T.fill(T.layout("cf")["title"], ye=long_date(cx.ye)), cx.unit_line)
    p.header()
    pyv = compute_cash_flow(cx, "PY")
    cx.precomputed.setdefault("cf", {})["PY"] = pyv
    cx.precomputed["cf"]["CY"] = compute_cash_flow(cx, "CY")

    def L(rid: str, caption: str, cy: str, alias: str | None = None, cap: str = "caption", py: str | None = None) -> None:
        # PY lines with a direct P&L / note source are linked to it (they then follow any fix of the PY statements);
        # working-capital and investing/financing PY lines are typed from the prior-year figures, as in real files
        p.line(rid, caption, {"CY": Fx(cy), "PY": Fx(py) if py else pyv[rid]}, cap=cap)
        p.index(rid, alias)

    def S(rid: str, caption: str, members: list[str], alias: str, signs: dict[str, int] | None = None, style: str = "amount_bold") -> None:
        p.total(rid, caption, members, signs=signs, style=style)
        p.index(rid, alias)

    def b(rid: str, k: str) -> str:
        return tok("BS", rid, k)

    p.section("A. Cash flow from operating activities")
    L("pbt", "Profit/(loss) before tax", tok("PL", "pbt", "CY"), "cf.pbt", py=tok("PL", "pbt", "PY"))
    p.section("Adjustments for:", style="caption")
    L("dep", "Depreciation and amortisation expense", tok("PL", "dep", "CY"), "cf.depreciation", cap="caption2", py=tok("PL", "dep", "PY"))
    L("fin", "Finance costs", tok("PL", "fin", "CY"), "cf.finance_costs", cap="caption2", py=tok("PL", "fin", "PY"))
    L("int_income", "Interest income", "-" + tok("Note 20-27", "oi.interest", "CY"), "cf.interest_income", cap="caption2",
      py="-" + tok("Note 20-27", "oi.interest", "PY"))
    S("op_before_wc", "Operating profit/(loss) before working capital changes", ["pbt", "dep", "fin", "int_income"], "cf.op_before_wc")
    p.section("Changes in working capital:", style="caption")
    L("wc_tr", "(Increase)/decrease in trade receivables", f"{b('tr', 'PY')}-{b('tr', 'CY')}", "cf.wc_tr", cap="caption2")
    L("wc_ofa", "(Increase)/decrease in other financial assets", f"({b('ofa.nc', 'PY')}+{b('ofa.c', 'PY')})-({b('ofa.nc', 'CY')}+{b('ofa.c', 'CY')})", "cf.wc_ofa", cap="caption2")
    L("wc_oa", "(Increase)/decrease in other assets", f"({b('onca', 'PY')}+{b('oca', 'PY')})-({b('onca', 'CY')}+{b('oca', 'CY')})", "cf.wc_oa", cap="caption2")
    L("wc_tp", "Increase/(decrease) in trade payables", f"({b('tp.msme', 'CY')}+{b('tp.others', 'CY')})-({b('tp.msme', 'PY')}+{b('tp.others', 'PY')})", "cf.wc_tp", cap="caption2")
    L("wc_ofl", "Increase/(decrease) in other financial liabilities", f"{b('ofl', 'CY')}-{b('ofl', 'PY')}", "cf.wc_ofl", cap="caption2")
    L("wc_ocl", "(Decrease)/increase in other current liabilities", f"{b('ocl', 'CY')}-{b('ocl', 'PY')}", "cf.ocl", cap="caption2")
    L("wc_prov", "Increase/(decrease) in provisions", f"({b('prov.nc', 'CY')}+{b('prov.c', 'CY')})-({b('prov.nc', 'PY')}+{b('prov.c', 'PY')})", "cf.wc_prov", cap="caption2")
    L("remeasure", "Remeasurement of defined benefit plans (recognised in other comprehensive income)", tok("PL", "oci.rem", "CY"), "cf.remeasurement", cap="caption2")
    S("cash_gen", "Cash generated from/(used in) operations", ["op_before_wc", "wc_tr", "wc_ofa", "wc_oa", "wc_tp", "wc_ofl", "wc_ocl", "wc_prov", "remeasure"], "cf.cash_generated")
    L("tax_paid", "Income taxes paid (net of refunds)", f"-({tok('PL', 'tax.cur', 'CY')}-({b('ctl', 'CY')}-{b('ctl', 'PY')}))", "cf.taxes_paid")
    S("net_a", "Net cash generated from/(used in) operating activities (A)", ["cash_gen", "tax_paid"], "cf.operating")
    p.blank()
    p.section("B. Cash flow from investing activities")
    n35 = "Note 3-5"
    L("ppe", "Purchase of property, plant and equipment (including capital work-in-progress)",
      f"-({tok(n35, 'g.add_cy', 'total')}+{tok(n35, 'c.add_cy', 'amt')}+{tok(n35, 'c.cap_cy', 'amt')})", "cf.purchase_ppe", cap="caption2")
    L("ppe_sale", "Proceeds from sale of property, plant and equipment", f"-{tok(n35, 'g.disp_cy', 'total')}+{tok(n35, 'd.disp_cy', 'total')}", "cf.proceeds_ppe", cap="caption2")
    L("intangibles", "Purchase of intangible assets", f"-{tok(n35, 'software.g.add_cy', 'amt')}", "cf.purchase_intangibles", cap="caption2")
    L("investments", "Investment in subsidiary", f"-({b('inv', 'CY')}-{b('inv', 'PY')})", "cf.investments", cap="caption2")
    L("int_recd", "Interest received", tok("Note 20-27", "oi.interest", "CY"), "cf.interest_received", cap="caption2")
    S("net_b", "Net cash generated from/(used in) investing activities (B)", ["ppe", "ppe_sale", "intangibles", "investments", "int_recd"], "cf.investing")
    p.blank()
    p.section("C. Cash flow from financing activities")
    n12, n14, n20 = "Note 12-13", "Note 14-19", "Note 20-27"
    L("shares", "Proceeds from issue of equity shares (including securities premium)", f"{tok(n12, 'rec.issued', 'amt_cy')}+{tok(n12, 'sp.issued', 'CY')}", "cf.share_issue", cap="caption2")
    L("borrow_in", "Proceeds from long-term borrowings", f"{tok(n14, 'fin.CY.proceeds', 'tl')}+{tok(n14, 'fin.CY.proceeds', 'hl')}", "cf.borrow_in", cap="caption2")
    L("borrow_out", "Repayment of long-term borrowings", f"{tok(n14, 'fin.CY.repaid', 'tl')}+{tok(n14, 'fin.CY.repaid', 'hl')}", "cf.borrow_out", cap="caption2")
    L("lease_principal", "Payment of principal portion of lease liabilities", f"{tok(n14, 'lease.pay', 'CY')}+{tok(n14, 'lease.int', 'CY')}", "cf.lease_principal", cap="caption2")
    L("lease_interest", "Payment of interest portion of lease liabilities", f"-{tok(n14, 'lease.int', 'CY')}", "cf.lease_interest", cap="caption2")
    L("interest_paid", "Interest paid on borrowings", f"-({tok(n20, 'fin.bank', 'CY')}+{tok(n20, 'fin.holding', 'CY')}-{tok(n14, 'fin.CY.noncash', 'hl')})", "cf.interest_paid", cap="caption2")
    S("net_c", "Net cash generated from/(used in) financing activities (C)", ["shares", "borrow_in", "borrow_out", "lease_principal", "lease_interest", "interest_paid"], "cf.financing")
    p.blank()
    S("net_change", "Net increase/(decrease) in cash and cash equivalents (A + B + C)", ["net_a", "net_b", "net_c"], "cf.net_change", signs={"net_a": 1, "net_b": 1, "net_c": 1})
    p.line("opening", "Cash and cash equivalents at the beginning of the year", {"CY": Fx(tok("CFS", "closing", "PY")), "PY": pyv["opening"]})
    p.index("opening", "cf.opening_cash")
    S("closing", "Cash and cash equivalents at the end of the year", ["net_change", "opening"], "cf.closing_cash", style="amount_total")
    p.blank()
    p.section("Components of cash and cash equivalents", style="caption_bold")
    p.line("comp_bank", "Balances with banks – in current accounts", {k: Fx(tok("Note 6-11", "cash.bank", k)) for k in ("CY", "PY")}, cap="caption2")
    p.index("comp_bank", "cf.components_bank")
    p.line("comp_hand", "Cash on hand", {k: Fx(tok("Note 6-11", "cash.hand", k)) for k in ("CY", "PY")}, cap="caption2")
    p.index("comp_hand", "cf.components_hand")
    S("comp_total", "Total cash and cash equivalents (refer note 11)", ["comp_bank", "comp_hand"], "cf.components_total")
    p.blank()
    p.put(p.r, 1, "Note: The cash flow statement has been prepared under the indirect method set out in Ind AS 7, Statement of Cash Flows.", "unit")
    p.r += 2
    sign_block(cx, p, 2, 3, statement=True)
    p.finish(last_col=3)
    return p


def policies_page(cx: BuildContext) -> Page:
    ent = cx.inp.entity
    # one text column that fits A4 portrait at 100 %: LibreOffice never shrinks a sheet whose print area is a
    # single column, however wide, because that column already counts as "one page wide"
    p = Page(cx.spec, "Policies", "policies", [ColSpec("x", 2, "")], code="pol", caption_width=92, amount_width=2)
    cx.pages["pol"] = p
    p.title(ent.name, T.fill(T.layout("notes")["title"], ye=long_date(cx.ye)), cx.unit_line)
    p.section("1. Corporate information", rid="note1")
    p.text(T.fill(T.wording("corporate_info")["text"], name=ent.name, reg_label=ent.registration_label, cin=ent.cin or "-", address=ent.address,
                  activity=ent.activity, holding=ent.holding or "its holding company"), rid="note1.text", merge=False)
    p.blank()
    p.section("2. Basis of preparation and material accounting policy information", rid="note2")
    for i, para in enumerate(T.wording("policies")["paragraphs"]):
        p.text(T.fill(para, unit_label=ent.unit_label), rid=f"note2.p{i}", merge=False)
    p.finish(last_col=1)
    return p


# --------------------------------------------------------------------------- orchestration


def build_spec(inp: PrepareInputs) -> BuildContext:
    spec = WorkbookSpec()
    spec.entity = {"name": inp.entity.name, "units": inp.units, "layout": inp.layout, "year_end": inp.entity.year_end.isoformat()}
    cx = BuildContext(spec=spec, inp=inp)
    cx.pages["tb"] = build_tb(cx)
    cx.pages["far"] = build_far(cx)
    cx.pages["ageing"] = build_ageing(cx)
    build_note_3_5(cx)
    build_note_6_11(cx)
    build_note_12_13(cx)
    build_note_14_19(cx)
    build_note_20_27(cx)
    build_rpt(cx)
    n2934 = build_note_29_34(cx)
    sign_block(cx, n2934, 3, 4, statement=False)
    n2934.finish(last_col=7)
    bs_page(cx)
    pl_page(cx)
    soce_page(cx)
    cf_page(cx)
    policies_page(cx)
    order = {n: i for i, n in enumerate(SHEET_ORDER)}
    spec.sheets.sort(key=lambda s: order.get(s.name, 99))
    resolve_tokens(spec, cx.pages)
    if inp.cast_safe_totals:
        make_cast_safe(spec)
    spec.templates = T.versions_used(
        ["bs", "pl_spfs" if inp.layout == "spfs" else "pl", "soce", "cf", "notes"],
        list(T.library()["notes"]),
        ["corporate_info", "policies", "gc", "msme", "borrowings_terms", "rpt_terms", "investments_scope", "fair_value", "events", "share_rights", "actuarial", "ratios_na"],
    )
    precompute_ratios(cx)
    return cx


def make_cast_safe(spec: WorkbookSpec) -> None:
    """Module P on real data: every printed total adds the *printed* components (02 §7.2 house form).

    Each unit-coefficient linear total becomes Σ ROUND(cell, 2) / SUMPRODUCT(ROUND(range, 2)),
    so casting holds by construction; Balance Sheet and cash-flow rounding gaps are then
    closed by the fix engine's balancing lines.
    """
    from fscore.engine import formula as F
    from fscore.engine.linear import linear_form

    for s in spec.sheets:
        if s.role in ("tb", "backup"):
            continue
        for (_r, _c), cs in s.cells.items():
            if not cs.formula or not cs.style.startswith("amount"):
                continue
            try:
                lf = linear_form(F.parse(cs.formula), s.name)
            except F.FormulaError:
                continue
            if lf is None or not lf.is_total or lf.constant:
                continue
            parts = []
            for t in lf.terms:
                body = f"ROUND({t.text},2)" if t.area.is_cell else f"SUMPRODUCT(ROUND({t.text},2))"
                parts.append(("-" if t.coef < 0 else "+") + body)
            cs.formula = "".join(parts).lstrip("+")


def precompute_ratios(cx: BuildContext) -> None:
    """Typed previous-year ratios (they need balances one year before the comparative period)."""
    tb = cx.inp.tb
    div = D(cx.div)

    def bal(period: str, *keys: str, sign: int = 1) -> Decimal:
        if period == "PY1":
            return sign * opening_key(cx, list(keys)) / div
        return sign * sum((tb.by_key(k, period) for k in keys), D(0)) / div

    ca_keys = ("tr.good.third_party", "tr.good.related", "tr.impaired", "tr.allowance", "cash.bank_current", "cash.on_hand",
               "ofa.c.interest_accrued", "ofa.c.unbilled", "ofa.c.recoverable_group", "oca.prepaid", "oca.govt_balances", "oca.advances_suppliers")
    cl_keys = ("borr.c.current_maturities", "lease.c", "tp.msme", "tp.others", "tp.related", "ofl.employee_payable", "ofl.interest_accrued",
               "ofl.capital_creditors", "ocl.statutory_dues", "ocl.advance_customers", "prov.c.gratuity", "prov.c.compensated_absences", "ctl.net")
    debt_keys = ("borr.nc.term_loan_bank", "borr.nc.loan_holding", "borr.c.current_maturities", "lease.nc", "lease.c")
    eq_keys = ("eq.share_capital", "oe.securities_premium", "oe.retained_earnings")
    cf = cx.precomputed["cf"]["PY"]
    pat_py = cf["pbt"] - bal("PY", "tax.current", "tax.deferred")
    oci_py = -bal("PY", "oci.remeasurement", "oci.tax")
    rev_py = -bal("PY", "rev.services_domestic", "rev.services_export", "rev.related_support")
    eq_py = -bal("PY", *eq_keys) + pat_py + oci_py  # TB RE is the opening balance
    eq_py1 = -bal("PY1", *eq_keys)
    ca_py, cl_py = bal("PY", *ca_keys), -bal("PY", *cl_keys)
    ca_py1, cl_py1 = bal("PY1", *ca_keys), -bal("PY1", *cl_keys)
    debt_py = -bal("PY", *debt_keys)
    tr_keys = ("tr.good.third_party", "tr.good.related", "tr.impaired", "tr.allowance")
    tp_keys = ("tp.msme", "tp.others", "tp.related")
    oth_py = bal("PY", "oth.legal_professional", "oth.travel", "oth.communication", "oth.software", "oth.ecl", "oth.auditor", "oth.support_services_group", "oth.misc")
    dep_py, fin_py = cf["dep"], cf["fin"]
    debt_service = -(cf["interest_paid"] + cf["lease_principal"] + cf["lease_interest"] + cf["borrow_out"])

    def safe(a: Decimal, b: Decimal) -> Decimal:
        return D(0) if b == 0 else a / b

    py = {
        "current_ratio": safe(ca_py, cl_py),
        "debt_equity": safe(debt_py, eq_py),
        "dscr": safe(pat_py + dep_py + fin_py, debt_service),
        "roe": safe(pat_py, (eq_py + eq_py1) / 2),
        "tr_turnover": safe(rev_py, (bal("PY", *tr_keys) + bal("PY1", *tr_keys)) / 2),
        "tp_turnover": safe(oth_py, (-bal("PY", *tp_keys) - bal("PY1", *tp_keys)) / 2),
        "nct": safe(rev_py, ((ca_py - cl_py) + (ca_py1 - cl_py1)) / 2),
        "npr": safe(pat_py, rev_py),
        "roce": safe(cf["pbt"] + fin_py, eq_py + debt_py),
    }
    cx.precomputed["ratio_py"] = {k: v.quantize(D("0.0001")) for k, v in py.items()}
    ratio_cells = cx.spec.entity.get("index", {})
    sh = cx.spec.sheet("Note 29-34")
    for key, *_rest in RATIOS:
        entry = ratio_cells.get(f"n2934.ratio.{key}")
        if entry:
            _name, row, cols = entry
            sh.set(row, cols["PY"], float(cx.precomputed["ratio_py"][key]), style=sh.cells[(row, cols["PY"])].style)


def displayed(values: dict[Key, Any], spec: WorkbookSpec, sheet: str, row: int, col: int) -> Decimal:
    v = values.get((sheet, row, col))
    if v is None:
        v = spec.constant((sheet, row, col))
    if v is None or isinstance(v, str):
        return D(0)
    cs = spec.sheet(sheet).cells.get((row, col))
    from fscore.prepare.style import STYLES

    fmt = STYLES.get(cs.style if cs else "amount", {}).get("num_format", ACCOUNTING)
    r = render(v, fmt)
    return r.value if r.value is not None else D(0)


def finalise(cx: BuildContext, *, values: dict[Key, Any] | None = None, narratives: bool = True,
             fc_values: dict[Key, Any] | None = None) -> dict[Key, Any]:
    """Compute cached values, derive dependent typed figures, write the typed narrative.

    ``fc_values``: figures the hidden functional-currency columns are derived from
    (the books before any INR-only error is injected); defaults to the final values.
    """
    spec = cx.spec
    vals = values if values is not None else compute_values(spec)
    # tax reconciliation balancing item: printed total tax − printed expected tax
    idx = spec.entity["index"]
    sheet, row_other, cols = idx["note.tax.rec_other"]
    _s, row_exp, _c = idx["n2027.taxrec.expected"]
    _s2, row_tot, cols_tot = idx["note.tax.total"]
    changed = False
    for k in ("CY", "PY"):
        expected = displayed(vals, spec, sheet, row_exp, cols[k])
        tax_total = displayed(vals, spec, sheet, row_tot, cols_tot[k])
        other = tax_total - expected
        cs = spec.sheet(sheet).cells[(row_other, cols[k])]
        if cs.value != float(other):
            cs.value = float(other)
            changed = True
    if changed:
        vals = compute_values(spec)
    if cx.spfs:
        fill_fc_columns(cx, fc_values if fc_values is not None else vals)
    if narratives:
        fill_narratives(cx, vals)
    return vals


def fill_fc_columns(cx: BuildContext, vals: dict[Key, Any]) -> None:
    """SPFS: hidden functional-currency helper columns on the Balance Sheet (typed source figures)."""
    spec = cx.spec
    fc_cols = spec.entity.get("fc_cols")
    if not fc_cols:
        return
    sh = spec.sheet("BS")
    for alias, (sheet, row, cols) in spec.entity["index"].items():
        if sheet != "BS" or not alias.startswith("bs."):
            continue
        for k in ("CY", "PY"):
            v = vals.get((sheet, row, cols[k]))
            if not isinstance(v, (int, float)):
                continue
            rate = cx.inp.fx.get(k, {}).get("closing", D(1))
            sh.set(row, fc_cols[k], float(D(str(v)) / rate), style="amount")


def _fmt(d: Decimal) -> str:
    return f"{abs(q2(d)):,.2f}"


def fill_narratives(cx: BuildContext, vals: dict[Key, Any]) -> None:
    spec = cx.spec
    idx = spec.entity["index"]

    def disp(alias: str, k: str) -> Decimal:
        s, r, cols = idx[alias]
        return displayed(vals, spec, s, r, cols[k])

    unit = UNIT_WORD.get(cx.inp.units, cx.inp.units)
    pat_cy, pat_py = disp("pl.pat", "CY"), disp("pl.pat", "PY")
    re_cy = disp("note.other_equity.re_close", "CY")
    nw_cy, nw_py = disp("bs.total_equity", "CY"), disp("bs.total_equity", "PY")
    wc_cy = disp("bs.ca_total", "CY") - disp("bs.cl_total", "CY")
    wc_py = disp("bs.ca_total", "PY") - disp("bs.cl_total", "PY")
    cash_cy = disp("bs.cash", "CY")
    cash_py = disp("bs.cash", "PY")
    s_issue, r_issue, c_issue = idx["soce.issue_cy"]
    infusion = displayed(vals, spec, s_issue, r_issue, c_issue["total"])
    gc = T.wording("gc")
    ent = cx.inp.entity
    facts = {
        "result_cy": "earned a profit" if pat_cy >= 0 else "incurred a loss", "pat_cy": _fmt(pat_cy),
        "result_py": "profit" if pat_py >= 0 else "loss", "pat_py": _fmt(pat_py),
        "re_label": "accumulated losses" if re_cy < 0 else "retained earnings", "re_amount": _fmt(re_cy),
        "nw_cy": _fmt(nw_cy), "nw_py": _fmt(nw_py),
        "wc_a": "current assets" if wc_cy >= 0 else "current liabilities", "wc_b": "current liabilities" if wc_cy >= 0 else "current assets",
        "wc_cy": _fmt(wc_cy), "wc_py": _fmt(wc_py), "cash_cy": _fmt(cash_cy), "cash_py": _fmt(cash_py), "infusion": _fmt(infusion),
        "unit": unit, "ye": long_date(cx.ye), "pye": long_date(cx.py), "holding": ent.holding or "the holding company",
    }
    spec.entity["gc_facts"] = dict(facts.items())
    spec.entity["gc_figures"] = {"pat_cy": str(pat_cy), "pat_py": str(pat_py), "re_cy": str(re_cy), "nw_cy": str(nw_cy), "nw_py": str(nw_py),
                                 "wc_cy": str(wc_cy), "wc_py": str(wc_py), "cash_cy": str(cash_cy), "cash_py": str(cash_py), "infusion": str(infusion)}
    sh = spec.sheet("Note 29-34")
    allow_cy = abs(disp("note.tr.allowance", "CY"))
    allow_py = abs(disp("note.tr.allowance", "PY"))
    s611 = spec.sheet("Note 6-11")
    _s, r_ecl, _c = idx["n611.tr.ecl_text"]
    s611.cells[(r_ecl, 1)].value = (f"The Company has recognised a loss allowance for expected credit losses of ₹ {_fmt(allow_cy)} {unit} "
                                   f"(as at {long_date(cx.py)}: ₹ {_fmt(allow_py)} {unit}) on trade receivables, using a provision matrix based on historical credit loss experience.")
    s2027 = spec.sheet("Note 20-27")
    _s, r_tax, _c = idx["n2027.tax.text"]
    s2027.cells[(r_tax, 1)].value = (
        "The Company has taxable profits for the year; deferred tax assets on unused tax losses are recognised to the extent it is probable that future taxable profit will be available."
        if pat_cy >= 0 else
        "As the Company has incurred a loss for the year, deferred tax assets on unused tax losses have been recognised only to the extent of deferred tax liabilities.")

    def set_text(alias: str, text: str) -> None:
        _s, r, _c = idx[alias]
        cs = sh.cells.get((r, 1))
        if cs is None:
            sh.set(r, 1, text, style="narrative")
        else:
            cs.value = text
        sh.row_heights[r] = 12.0 * (len(text) // 95 + 1) + 2

    set_text("n2934.gc.p1", T.fill(gc["p1"], **facts))
    set_text("n2934.gc.p2", T.fill(gc["p2"], **facts))
    set_text("n2934.gc.p3", T.fill(gc["p3"], **facts))
    for i, f in enumerate(gc["factors"]):
        set_text(f"n2934.gc.f{i}", T.fill(f, **facts))
    set_text("n2934.gc.conclusion", T.fill(gc["conclusion"], **facts))
    # ratio explanations (> 25 % variance must be explained, Schedule III)
    for key, _label, num, _den, _style in RATIOS:
        s, r, cols = idx[f"note.ratio.{key}"]
        v = vals.get((s, r, cols["var"]))
        var = D(str(v)) if isinstance(v, (int, float)) else D(0)
        cs = spec.sheet(s).cells[(r, cols["reason"])]
        if abs(var) > D("0.25"):
            direction = "increase" if var > 0 else "decrease"
            cs.value = f"The {direction} is mainly on account of the movement in {num.lower()} during the year."
        else:
            cs.value = "-"
