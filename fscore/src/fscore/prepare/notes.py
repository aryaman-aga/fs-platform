"""Note sheets (Notes 3–34) in house style. Every figure links to the TB backup,
the FAR / ageing backups, another note, or a statement — or is a typed
supplementary figure (actuarial report, share register, prior-year signed).
"""

from __future__ import annotations

from decimal import Decimal

from fscore.prepare import templates as T
from fscore.prepare.backups import CLASS_LABELS
from fscore.prepare.context import BuildContext, long_date
from fscore.prepare.layout import ColSpec, Fx, Page, tok

D = Decimal
PPE_KEYS = ("computers", "furniture", "office_equipment")


# --------------------------------------------------------------------------- helpers


def bs_cols(cx: BuildContext) -> list[ColSpec]:
    return [ColSpec("CY", 2, f"As at\n{long_date(cx.ye)}"), ColSpec("PY", 3, f"As at\n{long_date(cx.py)}")]


def yr_cols(cx: BuildContext) -> list[ColSpec]:
    return [ColSpec("CY", 2, f"Year ended\n{long_date(cx.ye)}"), ColSpec("PY", 3, f"Year ended\n{long_date(cx.py)}")]


def pl_cols(cx: BuildContext) -> list[ColSpec]:
    if cx.spfs:
        return [
            ColSpec("Q4_CY", 2, f"Three months ended\n{long_date(cx.ye)}"), ColSpec("Q4_PY", 3, f"Three months ended\n{long_date(cx.py)}"),
            ColSpec("CY", 4, f"Year ended\n{long_date(cx.ye)}"), ColSpec("PY", 5, f"Year ended\n{long_date(cx.py)}"),
        ]
    return yr_cols(cx)


def note_page(cx: BuildContext, name: str, code: str, cols: list[ColSpec], *, landscape: bool = False) -> Page:
    p = Page(cx.spec, name, "note", cols, code=code, caption_width=54, amount_width=15, landscape=landscape)
    p.title(cx.inp.entity.name, T.fill(T.layout("notes")["title"], ye=long_date(cx.ye)), cx.unit_line)
    cx.pages[code] = p
    return p


def title(p: Page, key: str) -> None:
    p.section(T.note_title(key), rid=f"title.{key}")




def tbline(cx: BuildContext, p: Page, rid: str, caption: str, keys: str | list[str], periods: list[str], *, sign: int | None = None, alias: str | None = None, cap: str = "caption", hidden: bool = False) -> int:
    row = p.line(rid, caption, {k: cx.tb(keys, k, sign) for k in periods}, cap=cap, lineage="tb:" + (keys if isinstance(keys, str) else ",".join(keys)), hidden=hidden)
    p.index(rid, alias)
    return row


def opening_key(cx: BuildContext, keys: str | list[str]) -> Decimal:
    ks = [keys] if isinstance(keys, str) else keys
    code_key = {a.code: a.key for a in cx.inp.tb.accounts}
    return sum((v for c, v in cx.inp.opening.items() if code_key.get(c) in ks), D(0))


def total(p: Page, rid: str, caption: str, members: list[str], *, alias: str | None = None, keys: list[str] | None = None, cap: str = "caption_bold", style: str = "amount_bold", signs: dict[str, int] | None = None) -> int:
    row = p.total(rid, caption, members, keys=keys, cap=cap, style=style, signs=signs)
    p.index(rid, alias)
    return row


# --------------------------------------------------------------------------- Note 3–5


def build_note_3_5(cx: BuildContext) -> Page:
    div = cx.div
    cols = [ColSpec(c, i + 2, CLASS_LABELS[c]) for i, c in enumerate(PPE_KEYS)] + [ColSpec("total", 5, "Total")]
    p = note_page(cx, "Note 3-5", "n35", cols)
    title(p, "ppe")
    p.section("(a) Property, plant and equipment", style="caption_bold")
    p.header("Particulars", note_label="")

    def far(yk: str, field: str, sign: str = "") -> dict[str, object]:
        out: dict[str, object] = {c: Fx(f"{sign}{tok('FAR', f'{yk}.{c}', field)}/{div}") for c in PPE_KEYS}
        out["total"] = Fx(f"SUM(B{p.r}:D{p.r})")
        return out

    p.section("Gross carrying amount", style="section")
    p.line("g.open_py", f"Balance as at {long_date(cx.py_open)}", far("PY", "open"))
    p.line("g.add_py", "Additions", far("PY", "add"))
    p.line("g.disp_py", "Disposals", far("PY", "disp", "-"))
    total(p, "g.close_py", f"Balance as at {long_date(cx.py)}", ["g.open_py", "g.add_py", "g.disp_py"], alias="note.ppe.gross_py")
    stale = "ppe_stale_june_row" in cx.inp.flags
    if stale:
        row = p.r
        vals: dict[str, object] = {c: Fx(f"{chr(64 + p.cols[c].col)}{p.row('g.close_py')}") for c in PPE_KEYS}
        vals["total"] = Fx(f"SUM(B{row}:D{row})")
        p.line("g.june", "Balance as at June 30, 2025", vals, cap="caption")
        p.index("g.june")
        cx.expected_rows["ppe_stale_june_row"] = (p.name, row)
    p.line("g.add_cy", "Additions", far("CY", "add"))
    p.index("g.add_cy", "note.ppe.additions_cy")
    p.line("g.disp_cy", "Disposals", far("CY", "disp", "-"))
    p.index("g.disp_cy", "note.ppe.disposals_cy")
    total(p, "g.close_cy", f"Balance as at {long_date(cx.ye)}", ["g.june" if stale else "g.close_py", "g.add_cy", "g.disp_cy"], alias="note.ppe.gross_cy")
    p.section("Accumulated depreciation", style="section")
    p.line("d.open_py", f"Balance as at {long_date(cx.py_open)}", far("PY", "dopen"))
    p.line("d.charge_py", "Charge for the year", far("PY", "charge"))
    p.line("d.disp_py", "Disposals", far("PY", "ddisp", "-"))
    total(p, "d.close_py", f"Balance as at {long_date(cx.py)}", ["d.open_py", "d.charge_py", "d.disp_py"])
    p.line("d.charge_cy", "Charge for the year", far("CY", "charge"))
    p.index("d.charge_cy", "note.ppe.depreciation_cy")
    p.line("d.disp_cy", "Disposals", far("CY", "ddisp", "-"))
    p.index("d.disp_cy", "note.ppe.dep_disposals_cy")
    total(p, "d.close_cy", f"Balance as at {long_date(cx.ye)}", ["d.close_py", "d.charge_cy", "d.disp_cy"])
    p.section("Net carrying amount", style="section")
    total(p, "n.py", f"As at {long_date(cx.py)}", ["g.close_py", "d.close_py"], signs={"d.close_py": -1}, alias="note.ppe.net_py")
    total(p, "n.cy", f"As at {long_date(cx.ye)}", ["g.close_cy", "d.close_cy"], signs={"d.close_cy": -1}, alias="note.ppe.net_cy")
    p.blank()
    # (b) CWIP — single amount column
    p.set_cols([ColSpec("amt", 2, "Amount")])
    p.section("(b) Capital work-in-progress", style="caption_bold")

    def cw(yk: str, field: str, sign: str = "") -> dict[str, object]:
        return {"amt": Fx(f"{sign}{tok('FAR', f'cwip.{yk}', field)}/{div}")}

    p.line("c.open_py", f"Balance as at {long_date(cx.py_open)}", cw("PY", "open"))
    p.line("c.add_py", "Additions", cw("PY", "add"))
    p.line("c.cap_py", "Capitalised during the year", cw("PY", "disp", "-"))
    total(p, "c.close_py", f"Balance as at {long_date(cx.py)}", ["c.open_py", "c.add_py", "c.cap_py"], alias="note.cwip.close_py")
    p.line("c.add_cy", "Additions", cw("CY", "add"))
    p.index("c.add_cy", "note.cwip.additions_cy")
    p.line("c.cap_cy", "Capitalised during the year", cw("CY", "disp", "-"))
    p.index("c.cap_cy", "note.cwip.capitalised_cy")
    total(p, "c.close_cy", f"Balance as at {long_date(cx.ye)}", ["c.close_py", "c.add_cy", "c.cap_cy"], alias="note.cwip.close_cy")
    p.blank()
    for key, attr, label in (("rou", "rou", "Buildings"), ("intangibles", "software", "Computer software")):
        title(p, key)
        p.set_cols([ColSpec("amt", 2, label)])
        p.header("Particulars", note_label="")
        pre = attr

        def fr(yk: str, field: str, sign: str = "", attr: str = attr) -> dict[str, object]:
            return {"amt": Fx(f"{sign}{tok('FAR', f'{attr}.{yk}', field)}/{div}")}

        p.section("Gross carrying amount")
        p.line(f"{pre}.g.open_py", f"Balance as at {long_date(cx.py_open)}", fr("PY", "open"))
        p.line(f"{pre}.g.add_py", "Additions", fr("PY", "add"))
        total(p, f"{pre}.g.close_py", f"Balance as at {long_date(cx.py)}", [f"{pre}.g.open_py", f"{pre}.g.add_py"])
        p.line(f"{pre}.g.add_cy", "Additions", fr("CY", "add"))
        p.index(f"{pre}.g.add_cy", f"note.{attr}.additions_cy")
        total(p, f"{pre}.g.close_cy", f"Balance as at {long_date(cx.ye)}", [f"{pre}.g.close_py", f"{pre}.g.add_cy"])
        p.section("Accumulated depreciation / amortisation")
        p.line(f"{pre}.d.open_py", f"Balance as at {long_date(cx.py_open)}", fr("PY", "dopen"))
        p.line(f"{pre}.d.charge_py", "Charge for the year", fr("PY", "charge"))
        total(p, f"{pre}.d.close_py", f"Balance as at {long_date(cx.py)}", [f"{pre}.d.open_py", f"{pre}.d.charge_py"])
        p.line(f"{pre}.d.charge_cy", "Charge for the year", fr("CY", "charge"))
        p.index(f"{pre}.d.charge_cy", f"note.{attr}.charge_cy")
        total(p, f"{pre}.d.close_cy", f"Balance as at {long_date(cx.ye)}", [f"{pre}.d.close_py", f"{pre}.d.charge_cy"])
        p.section("Net carrying amount")
        total(p, f"{pre}.n.py", f"As at {long_date(cx.py)}", [f"{pre}.g.close_py", f"{pre}.d.close_py"], signs={f"{pre}.d.close_py": -1}, alias=f"note.{attr}.net_py")
        total(p, f"{pre}.n.cy", f"As at {long_date(cx.ye)}", [f"{pre}.g.close_cy", f"{pre}.d.close_cy"], signs={f"{pre}.d.close_cy": -1}, alias=f"note.{attr}.net_cy")
        p.blank()
    p.finish(last_col=5)
    return p


# --------------------------------------------------------------------------- Note 6–11


def build_note_6_11(cx: BuildContext) -> Page:
    bp = ["CY", "PY"]
    p = note_page(cx, "Note 6-11", "n611", bs_cols(cx))
    ent = cx.inp.entity
    title(p, "investments")
    p.header()
    p.section("Unquoted equity instruments of subsidiary – at cost", style="caption_bold")
    tbline(cx, p, "inv.sub", f"Equity shares of {ent.subsidiary}" if ent.subsidiary else "Equity shares of subsidiary", "inv.nc.subsidiary", bp)
    total(p, "inv.total", "Total non-current investments", ["inv.sub"], alias="note.investments.total")
    p.line("inv.aggregate", "Aggregate amount of unquoted investments", {k: Fx(p.ref("inv.total", k)) for k in bp})
    p.blank()
    title(p, "ofa")
    p.header()
    p.section("(a) Non-current", style="caption_bold")
    tbline(cx, p, "ofa.nc.deposits", "Security deposits", "ofa.nc.security_deposits", bp)
    total(p, "ofa.nc.total", "Total", ["ofa.nc.deposits"], alias="note.ofa.nc_total")
    p.section("(b) Current", style="caption_bold")
    tbline(cx, p, "ofa.c.interest", "Interest accrued on deposits", "ofa.c.interest_accrued", bp)
    tbline(cx, p, "ofa.c.unbilled", "Unbilled revenue", "ofa.c.unbilled", bp, alias="note.ofa.unbilled")
    tbline(cx, p, "ofa.c.recoverable", "Recoverable from group companies (refer note 28)", "ofa.c.recoverable_group", bp, alias="note.ofa.recoverable_group")
    total(p, "ofa.c.total", "Total", ["ofa.c.interest", "ofa.c.unbilled", "ofa.c.recoverable"], alias="note.ofa.c_total")
    p.blank()
    # Note 8 — deferred tax movement
    title(p, "dta")
    p.set_cols(yr_cols(cx))
    p.header()
    p.line("dta.open", "Balance at the beginning of the year", {"CY": cx.tb("dta", "PY"), "PY": cx.raw(opening_key(cx, "dta"))})
    p.index("dta.open")
    tbline(cx, p, "dta.pl", "Credit/(charge) recognised in the statement of profit and loss", "tax.deferred", bp, sign=-1, alias="note.dta.pl")
    tbline(cx, p, "dta.oci", "Credit/(charge) recognised in other comprehensive income", "oci.tax", bp, sign=-1, alias="note.dta.oci")
    total(p, "dta.close", "Balance at the end of the year", ["dta.open", "dta.pl", "dta.oci"], alias="note.dta.close")
    p.blank()
    p.set_cols(bs_cols(cx))
    title(p, "other_assets")
    p.header()
    p.section("(a) Non-current", style="caption_bold")
    tbline(cx, p, "oa.nc.capadv", "Capital advances", "onca.capital_advances", bp)
    total(p, "oa.nc.total", "Total", ["oa.nc.capadv"], alias="note.other_assets.nc_total")
    p.section("(b) Current", style="caption_bold")
    tbline(cx, p, "oa.c.prepaid", "Prepaid expenses", "oca.prepaid", bp)
    tbline(cx, p, "oa.c.govt", "Balances with government authorities", "oca.govt_balances", bp)
    tbline(cx, p, "oa.c.adv", "Advances to suppliers", "oca.advances_suppliers", bp)
    total(p, "oa.c.total", "Total", ["oa.c.prepaid", "oa.c.govt", "oa.c.adv"], alias="note.other_assets.c_total")
    p.blank()
    # Note 10 — trade receivables + ageing
    title(p, "tr")
    p.header()
    tbline(cx, p, "tr.good", "Unsecured, considered good", ["tr.good.third_party", "tr.good.related"], bp, alias="note.tr.good")
    tbline(cx, p, "tr.impaired", "Credit impaired", "tr.impaired", bp, alias="note.tr.impaired")
    total(p, "tr.gross", "Total", ["tr.good", "tr.impaired"], alias="note.tr.gross", cap="caption_bold")
    tbline(cx, p, "tr.allowance", "Less: Allowance for expected credit loss", "tr.allowance", bp, alias="note.tr.allowance")
    total(p, "tr.net", "Total trade receivables", ["tr.gross", "tr.allowance"], alias="note.tr.net")
    tbline(cx, p, "tr.related", "Of the above, receivables from related parties (refer note 28)", "tr.good.related", bp, alias="note.tr.related", cap="caption2")
    p.text("", rid="tr.ecl_text")
    p.index("tr.ecl_text")
    rb = ["Not due", "Less than 6 months", "6 months - 1 year", "1-2 years", "2-3 years", "More than 3 years"]
    p.set_cols([ColSpec(b, i + 2, b) for i, b in enumerate(rb)] + [ColSpec("total", 8, "Total")])
    p.section("(a) Trade receivables ageing schedule (outstanding from the due date of payment)", style="caption_bold")
    p.header("Particulars", note_label="")
    for yk in ("CY", "PY"):
        p.section(f"As at {long_date(cx.ye if yk == 'CY' else cx.py)}", rid=f"ageing.{yk}.head")
        members = []
        for kind, label in (("good", "Undisputed trade receivables – considered good"), ("impaired", "Undisputed trade receivables – credit impaired")):
            row = p.r
            vals: dict[str, object] = {b: Fx(f"{tok('Ageing', f'tr.{yk}.{kind}', b)}/{cx.div}") for b in rb}
            vals["total"] = Fx(f"SUM(B{row}:G{row})")
            rid = f"ageing.{yk}.{kind}"
            p.line(rid, label, vals)
            p.index(rid)
            members.append(rid)
        if "ageing_total_missing" in cx.inp.flags and yk == "CY":
            row = p.r
            vals = {b: Fx(f"SUM({chr(65 + i + 1)}{row - 2}:{chr(65 + i + 1)}{row - 1})") for i, b in enumerate(rb[:4])}
            vals["total"] = Fx(f"SUM(B{row}:G{row})")
            p.line(f"ageing.{yk}.total", "Total", vals, cap="caption_bold", style="amount_bold")
            p.index(f"ageing.{yk}.total", f"note.tr.ageing_total_{yk.lower()}")
            cx.expected_rows["ageing_total_missing"] = (p.name, row)
        else:
            total(p, f"ageing.{yk}.total", "Total", members, alias=f"note.tr.ageing_total_{yk.lower()}")
    p.blank()
    p.set_cols(bs_cols(cx))
    title(p, "cash")
    p.header()
    tbline(cx, p, "cash.bank", "Balances with banks – in current accounts", "cash.bank_current", bp, alias="note.cash.bank")
    tbline(cx, p, "cash.hand", "Cash on hand", "cash.on_hand", bp, alias="note.cash.on_hand")
    total(p, "cash.total", "Total cash and cash equivalents", ["cash.bank", "cash.hand"], alias="note.cash.total")
    p.finish(last_col=8)
    return p


# --------------------------------------------------------------------------- Note 12–13


def build_note_12_13(cx: BuildContext) -> Page:
    bp = ["CY", "PY"]
    ent = cx.inp.entity
    p = note_page(cx, "Note 12-13", "n1213", bs_cols(cx))
    sh_cy, sh_py = cx.inp.years["CY"].shares, cx.inp.years["PY"].shares
    fv = int(sh_cy.get("face_value", 10))
    title(p, "share_capital")
    p.header()
    p.section("Authorised", style="caption_bold")
    auth = 20_000_000
    p.line("sc.auth", f"{auth:,} (as at {long_date(cx.py)}: {auth:,}) equity shares of ₹{fv} each",
           {k: cx.raw(D(auth * fv)) for k in bp})
    p.section("Issued, subscribed and fully paid-up", style="caption_bold")
    tbline(cx, p, "sc.issued", f"{sh_cy['closing']:,} (as at {long_date(cx.py)}: {sh_py['closing']:,}) equity shares of ₹{fv} each", "eq.share_capital", bp, alias="note.share_capital.issued")
    total(p, "sc.total", "Total equity share capital", ["sc.issued"], alias="note.share_capital.total")
    p.blank()
    cols = [ColSpec("sh_cy", 2, f"Number of shares\n{long_date(cx.ye)}", "shares"), ColSpec("amt_cy", 3, f"Amount\n{long_date(cx.ye)}"),
            ColSpec("sh_py", 4, f"Number of shares\n{long_date(cx.py)}", "shares"), ColSpec("amt_py", 5, f"Amount\n{long_date(cx.py)}")]
    p.set_cols(cols)
    p.section("(a) Reconciliation of the number of shares and amount outstanding", style="caption_bold")
    p.header("Particulars", note_label="")

    def rec(rid: str, label: str, shares_cy: int, shares_py: int) -> None:
        row = p.r
        p.line(rid, label, {"sh_cy": shares_cy, "amt_cy": Fx(f"B{row}*{fv}/{cx.div}"), "sh_py": shares_py, "amt_py": Fx(f"D{row}*{fv}/{cx.div}")})
        p.index(rid)

    rec("rec.open", "At the beginning of the year", int(sh_cy["opening"]), int(sh_py["opening"]))
    members = ["rec.open"]
    if "share_june_subtotal" in cx.inp.flags:
        row = p.r
        p.line("rec.june", "Balance as at June 30, 2025", {"sh_cy": Fx(f"B{p.row('rec.open')}"), "amt_cy": Fx(f"B{row}*{fv}/{cx.div}"),
                                                            "sh_py": Fx(f"D{p.row('rec.open')}"), "amt_py": Fx(f"D{row}*{fv}/{cx.div}")})
        p.index("rec.june")
        members.append("rec.june")
    rec("rec.issued", "Issued during the year", int(sh_cy["issued"]), int(sh_py["issued"]))
    members.append("rec.issued")
    row = p.r
    p.line("rec.close", "At the end of the year", {
        "sh_cy": Fx(f"SUM(B{p.row(members[0])}:B{p.row(members[-1])})"), "amt_cy": Fx(f"B{row}*{fv}/{cx.div}"),
        "sh_py": Fx(f"SUM(D{p.row(members[0])}:D{p.row(members[-1])})"), "amt_py": Fx(f"D{row}*{fv}/{cx.div}"),
    }, cap="caption_bold")
    p.index("rec.close", "note.share_capital.rec_close")
    if "share_june_subtotal" in cx.inp.flags:
        cx.expected_rows["share_june_subtotal"] = (p.name, row)
    p.blank()
    p.set_cols(bs_cols(cx))
    p.section("(b) Rights, preferences and restrictions attached to equity shares", style="caption_bold")
    p.text(T.wording("share_rights")["text"])
    p.blank()
    holding = ent.holding or "Holding company"
    nominee = 1
    p.set_cols([ColSpec("sh_cy", 2, f"Number of shares\n{long_date(cx.ye)}", "shares"), ColSpec("pct_cy", 3, "% holding", "pct"),
                ColSpec("sh_py", 4, f"Number of shares\n{long_date(cx.py)}", "shares"), ColSpec("pct_py", 5, "% holding", "pct")])
    p.section("(c) Shares held by the holding company and shareholders holding more than 5% of the shares", style="caption_bold")
    p.header("Name of the shareholder", note_label="")
    row = p.r
    rc = p.row("rec.close")
    p.line("hold.holding", holding, {"sh_cy": int(sh_cy["closing"]) - nominee, "pct_cy": Fx(f"B{row}/B{rc}"),
                                     "sh_py": int(sh_py["closing"]) - nominee, "pct_py": Fx(f"D{row}/D{rc}")})
    p.index("hold.holding", "note.share_capital.holding_pct")
    p.blank()
    p.set_cols([ColSpec("sh_cy", 2, "Number of shares", "shares"), ColSpec("pct_cy", 3, "% of total shares", "pct"), ColSpec("chg", 4, "% change during the year", "pct")])
    p.section("(d) Shares held by promoters at the end of the year", style="caption_bold")
    p.header("Promoter name", note_label="")
    row = p.r
    hr = p.row("hold.holding")
    p.line("prom.holding", holding, {"sh_cy": Fx(f"B{hr}"), "pct_cy": Fx(f"B{row}/B{rc}"), "chg": Fx(f"C{row}-E{hr}")})
    p.index("prom.holding", "note.share_capital.promoter_pct")
    p.blank()
    # Note 13 — other equity
    p.set_cols(yr_cols(cx))
    title(p, "other_equity")
    p.header()
    p.section("Securities premium", style="caption_bold")
    # CY opening is linked to the PY closing so a change to PY (e.g. a balancing adjustment) carries into CY opening equity
    p.line("sp.open", "Balance at the beginning of the year", {"CY": Fx(tok(p.name, "sp.close", "PY")), "PY": cx.raw(-opening_key(cx, "oe.securities_premium"))})
    p.index("sp.open")
    prem_cy = D(int(sh_cy["issued"])) * D(sh_cy.get("premium_per_share", 0))
    prem_py = D(int(sh_py["issued"])) * D(sh_py.get("premium_per_share", 0))
    p.line("sp.issued", "Add: Premium on shares issued during the year", {"CY": cx.raw(prem_cy), "PY": cx.raw(prem_py)})
    p.index("sp.issued", "note.other_equity.premium_issued")
    total(p, "sp.close", "Balance at the end of the year", ["sp.open", "sp.issued"], alias="note.other_equity.sp_close")
    p.section("Retained earnings", style="caption_bold")
    tbline(cx, p, "re.open", "Balance at the beginning of the year", "oe.retained_earnings", bp, alias="note.other_equity.re_open")
    p.line("re.profit", "Profit/(loss) for the year", {k: Fx(tok("PL", "pat", k)) for k in bp})
    p.index("re.profit")
    p.line("re.oci", "Other comprehensive income for the year (net of tax)", {k: Fx(tok("PL", "oci.total", k)) for k in bp})
    p.index("re.oci")
    total(p, "re.close", "Balance at the end of the year", ["re.open", "re.profit", "re.oci"], alias="note.other_equity.re_close")
    if "CY" in bp and "PY" in bp:
        p.put(p.row("re.open"), p.col_of("re.open", "CY"), Fx(tok(p.name, "re.close", "PY")), "amount", lineage="link:re.close(PY)")
    total(p, "oe.total", "Total other equity", ["sp.close", "re.close"], signs={"sp.close": 1, "re.close": 1}, alias="note.other_equity.total", style="amount_total")
    p.finish(last_col=5)
    return p


# --------------------------------------------------------------------------- Note 14–19


def build_note_14_19(cx: BuildContext) -> Page:
    bp = ["CY", "PY"]
    p = note_page(cx, "Note 14-19", "n1419", bs_cols(cx))
    years = cx.inp.years
    title(p, "borrowings")
    p.header()
    p.section("(a) Non-current borrowings", style="caption_bold")
    tbline(cx, p, "borr.nc.tl", "Secured – term loan from bank", "borr.nc.term_loan_bank", bp, alias="note.borrowings.term_loan_nc")
    tbline(cx, p, "borr.nc.hl", "Unsecured – loan from holding company (refer note 28)", "borr.nc.loan_holding", bp, alias="note.borrowings.holding_loan")
    total(p, "borr.nc.total", "Total non-current borrowings", ["borr.nc.tl", "borr.nc.hl"], alias="note.borrowings.nc_total")
    p.section("(b) Current borrowings", style="caption_bold")
    tbline(cx, p, "borr.c.cm", "Current maturities of long-term borrowings", "borr.c.current_maturities", bp)
    total(p, "borr.c.total", "Total current borrowings", ["borr.c.cm"], alias="note.borrowings.c_total")
    p.section("(c) Terms of repayment and security", style="caption_bold")
    for i, para in enumerate(T.wording("borrowings_terms")["paragraphs"]):
        p.text(para, rid=f"borr.terms.{i}")
    p.blank()
    cols = [ColSpec("tl", 2, "Term loan from bank"), ColSpec("hl", 3, "Loan from holding company"), ColSpec("lease", 4, "Lease liabilities"), ColSpec("total", 5, "Total")]
    p.set_cols(cols)
    p.section("(d) Changes in liabilities arising from financing activities (Ind AS 7)", style="caption_bold")
    p.header("Particulars", note_label="")
    for yk in ("PY", "CY"):  # chronological, like the asset roll-forwards
        yd = years[yk]
        tl, hl, ls = yd.term_loan, yd.holding_loan, yd.lease
        p.section(f"Year ended {long_date(cx.ye if yk == 'CY' else cx.py)}")
        rows = [
            (f"fin.{yk}.open", "Balance at the beginning of the year", tl["opening"], hl["opening"], ls["opening"]),
            (f"fin.{yk}.proceeds", "Cash flows – proceeds", tl["drawn"], hl["received"], D(0)),
            (f"fin.{yk}.repaid", "Cash flows – repayments / lease payments", -tl["repaid"], -hl["repaid"], -ls["payments"]),
            (f"fin.{yk}.noncash", "Non-cash – interest credited (net of TDS) / interest accrued", D(0), hl["credited"], ls["interest"]),
            (f"fin.{yk}.newlease", "Non-cash – new leases", D(0), D(0), ls["additions"]),
        ]
        members = []
        for rid, label, a, b, c in rows:
            row = p.r
            p.line(rid, label, {"tl": cx.raw(a), "hl": cx.raw(b), "lease": cx.raw(c), "total": Fx(f"SUM(B{row}:D{row})")})
            p.index(rid)
            members.append(rid)
        total(p, f"fin.{yk}.close", "Balance at the end of the year", members, alias=f"note.financing.close_{yk.lower()}")
    p.blank()
    # Note 15 leases
    p.set_cols(yr_cols(cx))
    title(p, "lease")
    p.section("(a) Movement in lease liabilities", style="caption_bold")
    p.header()
    p.line("lease.open", "Balance at the beginning of the year", {"CY": cx.tb(["lease.nc", "lease.c"], "PY"), "PY": cx.raw(-opening_key(cx, ["lease.nc", "lease.c"]))})
    p.index("lease.open")
    p.line("lease.add", "Additions", {k: cx.raw(years[k].lease["additions"]) for k in bp})
    p.index("lease.add", "note.lease.additions")
    tbline(cx, p, "lease.int", "Finance cost accrued during the year", "fin.interest_lease", bp, sign=1, alias="note.lease.interest")
    p.line("lease.pay", "Payment of lease liabilities", {k: cx.raw(-years[k].lease["payments"]) for k in bp})
    p.index("lease.pay", "note.lease.payments")
    total(p, "lease.close", "Balance at the end of the year", ["lease.open", "lease.add", "lease.int", "lease.pay"], alias="note.lease.close")
    p.set_cols(bs_cols(cx))
    p.section("(b) Current and non-current classification", style="caption_bold")
    tbline(cx, p, "lease.c", "Current lease liabilities", "lease.c", bp, alias="note.lease.current")
    tbline(cx, p, "lease.nc", "Non-current lease liabilities", "lease.nc", bp, alias="note.lease.noncurrent")
    total(p, "lease.split", "Total lease liabilities", ["lease.c", "lease.nc"], alias="note.lease.total")
    p.section("(c) Maturity analysis – contractual undiscounted cash flows", style="caption_bold")
    mrows = []
    for i, (label, _v) in enumerate(years["CY"].lease_maturity.items()):
        rid = f"lease.mat.{i}"
        p.line(rid, label, {k: cx.raw(years[k].lease_maturity.get(label, D(0))) for k in bp})
        p.index(rid)
        mrows.append(rid)
        if i == 0 and "maturity_hidden_lease_row" in cx.inp.flags:
            row = p.r
            p.line("lease.mat.hidden", "Lease – earlier office premises (vacated)", {"CY": D("12.34"), "PY": D("12.34")}, hidden=True)
            p.index("lease.mat.hidden")
            mrows.append("lease.mat.hidden")
            cx.expected_rows["maturity_hidden_lease_row"] = (p.name, row)
    total(p, "lease.mat.total", "Total undiscounted lease liabilities", mrows, alias="note.lease.maturity_total")
    p.blank()
    # Note 16 provisions
    title(p, "provisions")
    p.header()
    p.section("(a) Non-current", style="caption_bold")
    tbline(cx, p, "prov.nc.gr", "Provision for employee benefits – gratuity", "prov.nc.gratuity", bp, alias="note.provisions.gratuity_nc")
    total(p, "prov.nc.total", "Total", ["prov.nc.gr"], alias="note.provisions.nc_total")
    p.section("(b) Current", style="caption_bold")
    tbline(cx, p, "prov.c.gr", "Provision for employee benefits – gratuity", "prov.c.gratuity", bp, alias="note.provisions.gratuity_c")
    tbline(cx, p, "prov.c.ca", "Provision for compensated absences", "prov.c.compensated_absences", bp)
    total(p, "prov.c.total", "Total", ["prov.c.gr", "prov.c.ca"], alias="note.provisions.c_total")
    p.set_cols(yr_cols(cx))
    p.section("(c) Defined benefit plan – gratuity (unfunded)", style="caption_bold")
    p.section("Changes in the present value of the defined benefit obligation", style="caption")
    gcy, gpy = years["CY"].gratuity, years["PY"].gratuity
    for rid, label, field, sgn in (("dbo.open", "Obligation at the beginning of the year", "opening", 1), ("dbo.sc", "Current service cost", "service_cost", 1),
                                   ("dbo.ic", "Interest cost", "interest_cost", 1), ("dbo.rem", "Remeasurement losses/(gains)", "remeasurement", 1),
                                   ("dbo.paid", "Benefits paid", "benefits_paid", -1)):
        p.line(rid, label, {"CY": cx.raw(sgn * gcy[field]), "PY": cx.raw(sgn * gpy[field])})
        p.index(rid, f"note.dbo.{field}")
    total(p, "dbo.close", "Obligation at the end of the year", ["dbo.open", "dbo.sc", "dbo.ic", "dbo.rem", "dbo.paid"], alias="note.dbo.close")
    p.line("dbo.cur", "Current portion (refer (b) above)", {k: Fx(p.ref("prov.c.gr", k)) for k in bp})
    p.index("dbo.cur", "note.dbo.current")
    p.line("dbo.nc", "Non-current portion (refer (a) above)", {k: Fx(p.ref("prov.nc.gr", k)) for k in bp})
    p.index("dbo.nc", "note.dbo.noncurrent")
    total(p, "dbo.split", "Total defined benefit obligation", ["dbo.cur", "dbo.nc"], alias="note.dbo.split_total")
    p.section("Expense recognised in the statement of profit and loss", style="caption")
    p.line("dbo.exp.sc", "Current service cost", {k: Fx(p.ref("dbo.sc", k)) for k in bp})
    p.line("dbo.exp.ic", "Interest cost", {k: Fx(p.ref("dbo.ic", k)) for k in bp})
    total(p, "dbo.exp.total", "Total expense (refer note 22)", ["dbo.exp.sc", "dbo.exp.ic"], alias="note.dbo.expense")
    p.line("dbo.oci", "Remeasurement losses/(gains) recognised in other comprehensive income", {k: Fx(p.ref("dbo.rem", k)) for k in bp})
    p.index("dbo.oci", "note.dbo.oci")
    p.text(T.wording("actuarial")["text"], rid="dbo.assumptions")
    p.blank()
    # Note 17 trade payables
    p.set_cols(bs_cols(cx))
    title(p, "tp")
    p.header()
    tbline(cx, p, "tp.msme", "Total outstanding dues of micro enterprises and small enterprises", "tp.msme", bp, alias="note.tp.msme")
    tbline(cx, p, "tp.others", "Total outstanding dues of creditors other than micro enterprises and small enterprises", ["tp.others", "tp.related"], bp, alias="note.tp.others")
    total(p, "tp.total", "Total trade payables", ["tp.msme", "tp.others"], alias="note.tp.total")
    tbline(cx, p, "tp.related", "Of the above, payable to related parties (refer note 28)", "tp.related", bp, alias="note.tp.related", cap="caption2")
    pb = ["Not due", "Less than 1 year", "1-2 years", "2-3 years", "More than 3 years"]
    p.set_cols([ColSpec(b, i + 2, b) for i, b in enumerate(pb)] + [ColSpec("total", 7, "Total")])
    p.section("(a) Trade payables ageing schedule (outstanding from the due date of payment)", style="caption_bold")
    p.header("Particulars", note_label="")
    for yk in ("CY", "PY"):
        p.section(f"As at {long_date(cx.ye if yk == 'CY' else cx.py)}")
        members = []
        for kind, label in (("MSME", "MSME"), ("Others", "Others")):
            row = p.r
            vals: dict[str, object] = {b: Fx(f"{tok('Ageing', f'tp.{yk}.{kind}', b)}/{cx.div}") for b in pb}
            vals["total"] = Fx(f"SUM(B{row}:F{row})")
            rid = f"tpage.{yk}.{kind}"
            p.line(rid, label, vals)
            p.index(rid)
            members.append(rid)
        for code, kind in (("msme", "Disputed dues – MSME"), ("others", "Disputed dues – Others")):
            row = p.r
            rid = f"tpage.{yk}.{code}.disputed"
            vals = dict.fromkeys(pb, 0)
            vals["total"] = Fx(f"SUM(B{row}:F{row})")
            p.line(rid, kind, vals)
            members.append(rid)
        total(p, f"tpage.{yk}.total", "Total", members, alias=f"note.tp.ageing_total_{yk.lower()}")
    p.set_cols(bs_cols(cx))
    p.section("(b) Dues to micro and small enterprises (MSMED Act, 2006)", style="caption_bold")
    p.line("msme.principal", "(i) Principal amount remaining unpaid to suppliers at the end of the year", {k: Fx(p.ref("tp.msme", k)) for k in bp})
    p.index("msme.principal", "note.msme.principal")
    for i, label in enumerate(["(ii) Interest due thereon remaining unpaid", "(iii) Interest paid under section 16 along with amounts paid beyond the appointed day",
                               "(iv) Interest due and payable for the period of delay", "(v) Interest accrued and remaining unpaid",
                               "(vi) Further interest remaining due and payable in succeeding years"]):
        p.line(f"msme.{i}", label, dict.fromkeys(bp, 0))
    p.text(T.wording("msme")["text"], rid="msme.text")
    p.blank()
    title(p, "ofl")
    p.header()
    tbline(cx, p, "ofl.emp", "Employee benefits payable", "ofl.employee_payable", bp)
    tbline(cx, p, "ofl.int", "Interest accrued on borrowings", "ofl.interest_accrued", bp)
    tbline(cx, p, "ofl.cap", "Capital creditors", "ofl.capital_creditors", bp)
    total(p, "ofl.total", "Total other financial liabilities", ["ofl.emp", "ofl.int", "ofl.cap"], alias="note.ofl.total")
    p.blank()
    title(p, "ocl")
    p.header()
    tbline(cx, p, "ocl.stat", "Statutory dues payable", "ocl.statutory_dues", bp, alias="note.ocl.statutory")
    tbline(cx, p, "ocl.adv", "Advance from customers (contract liabilities)", "ocl.advance_customers", bp, alias="note.ocl.advance_customers")
    total(p, "ocl.total", "Total other current liabilities", ["ocl.stat", "ocl.adv"], alias="note.ocl.total")
    p.finish(last_col=7)
    return p


# --------------------------------------------------------------------------- Note 20–27


def build_note_20_27(cx: BuildContext) -> Page:
    pp = cx.pl_periods
    bp = ["CY", "PY"]
    p = note_page(cx, "Note 20-27", "n2027", pl_cols(cx), landscape=cx.spfs)
    title(p, "revenue")
    p.header()
    tbline(cx, p, "rev.services", "Sale of services", ["rev.services_domestic", "rev.services_export"], pp, alias="note.revenue.services")
    tbline(cx, p, "rev.group", "Support services to group companies (refer note 28)", "rev.related_support", pp, alias="note.revenue.group")
    total(p, "rev.total", "Total revenue from operations", ["rev.services", "rev.group"], alias="note.revenue.total")
    p.section("(a) Disaggregation of revenue from contracts with customers – by geography", style="caption_bold")
    tbline(cx, p, "rev.geo.india", "India", "rev.services_domestic", pp, alias="note.revenue.india")
    tbline(cx, p, "rev.geo.overseas", "Outside India", ["rev.services_export", "rev.related_support"], pp, alias="note.revenue.overseas")
    total(p, "rev.geo.total", "Total revenue from contracts with customers", ["rev.geo.india", "rev.geo.overseas"], alias="note.revenue.disaggregation_total")
    p.set_cols(bs_cols(cx) if not cx.spfs else [ColSpec("CY", 4, f"As at\n{long_date(cx.ye)}"), ColSpec("PY", 5, f"As at\n{long_date(cx.py)}")])
    p.section("(b) Contract balances", style="caption_bold")
    p.line("rev.cb.tr", "Trade receivables (refer note 10)", {k: Fx(tok("Note 6-11", "tr.net", k)) for k in bp})
    p.line("rev.cb.unbilled", "Unbilled revenue (refer note 7)", {k: Fx(tok("Note 6-11", "ofa.c.unbilled", k)) for k in bp})
    p.line("rev.cb.cl", "Contract liabilities – advance from customers (refer note 19)", {k: Fx(tok("Note 14-19", "ocl.adv", k)) for k in bp})
    p.blank()
    p.set_cols(pl_cols(cx))
    title(p, "other_income")
    p.header()
    tbline(cx, p, "oi.interest", "Interest income on bank deposits", "oi.interest_deposits", pp, alias="note.other_income.interest")
    tbline(cx, p, "oi.misc", "Miscellaneous income", "oi.misc", pp)
    total(p, "oi.total", "Total other income", ["oi.interest", "oi.misc"], alias="note.other_income.total")
    p.blank()
    title(p, "employee")
    p.header()
    tbline(cx, p, "emp.sal", "Salaries, wages and bonus", "emp.salaries", pp)
    tbline(cx, p, "emp.pf", "Contribution to provident and other funds", "emp.pf", pp)
    tbline(cx, p, "emp.gr", "Gratuity (refer note 16)", "emp.gratuity", pp, alias="note.employee.gratuity")
    tbline(cx, p, "emp.welfare", "Staff welfare expenses", "emp.welfare", pp)
    total(p, "emp.total", "Total employee benefits expense", ["emp.sal", "emp.pf", "emp.gr", "emp.welfare"], alias="note.employee.total")
    p.blank()
    title(p, "finance")
    p.header()
    tbline(cx, p, "fin.bank", "Interest on term loan", "fin.interest_bank", pp, alias="note.finance.bank")
    tbline(cx, p, "fin.holding", "Interest on loan from holding company (refer note 28)", "fin.interest_holding", pp, alias="note.finance.holding")
    tbline(cx, p, "fin.lease", "Interest on lease liabilities", "fin.interest_lease", pp, alias="note.finance.lease")
    total(p, "fin.total", "Total finance costs", ["fin.bank", "fin.holding", "fin.lease"], alias="note.finance.total")
    p.blank()
    title(p, "depreciation")
    p.header()
    tbline(cx, p, "dep.ppe", "Depreciation on property, plant and equipment (refer note 3)", "dep.ppe", pp, alias="note.depreciation.ppe")
    tbline(cx, p, "dep.rou", "Depreciation on right-of-use assets (refer note 4)", "dep.rou", pp, alias="note.depreciation.rou")
    tbline(cx, p, "dep.int", "Amortisation of intangible assets (refer note 5)", "dep.intangibles", pp, alias="note.depreciation.intangibles")
    total(p, "dep.total", "Total depreciation and amortisation expense", ["dep.ppe", "dep.rou", "dep.int"], alias="note.depreciation.total")
    p.blank()
    title(p, "other_expenses")
    p.header()
    lines = [("oth.legal", "Legal and professional fees", "oth.legal_professional"), ("oth.travel", "Travelling and conveyance", "oth.travel"),
             ("oth.comm", "Communication expenses", "oth.communication"), ("oth.software", "Software subscription charges", "oth.software"),
             ("oth.ecl", "Provision for expected credit loss", "oth.ecl"), ("oth.auditor", "Payment to auditors (refer (a) below)", "oth.auditor"),
             ("oth.group", "Support services from group companies (refer note 28)", "oth.support_services_group"),
             ("oth.misc", "Miscellaneous expenses", "oth.misc")]
    for rid, label, key in lines:
        tbline(cx, p, rid, label, key, pp, alias={"oth.misc": "note.other_expenses.misc", "oth.group": "note.other_expenses.group", "oth.auditor": "note.other_expenses.auditor"}.get(rid))
    total(p, "oth.total", "Total other expenses", [r for r, _l, _k in lines], alias="note.other_expenses.total")
    p.section("(a) Payment to auditors", style="caption_bold")
    p.line("aud.statutory", "As auditor – statutory audit", {k: Fx(p.ref("oth.auditor", k)) for k in pp})
    p.line("aud.other", "For other services", dict.fromkeys(pp, 0))
    total(p, "aud.total", "Total", ["aud.statutory", "aud.other"], alias="note.auditor.total")
    p.blank()
    title(p, "tax")
    p.header()
    p.section("(a) Income tax expense recognised in profit or loss", style="caption_bold")
    tbline(cx, p, "tax.current", "Current tax", "tax.current", pp, alias="note.tax.current")
    tbline(cx, p, "tax.deferred", "Deferred tax", "tax.deferred", pp, alias="note.tax.deferred")
    total(p, "tax.total", "Total income tax expense", ["tax.current", "tax.deferred"], alias="note.tax.total")
    fy_cols = yr_cols(cx) if not cx.spfs else [ColSpec("CY", 4, f"Year ended\n{long_date(cx.ye)}"), ColSpec("PY", 5, f"Year ended\n{long_date(cx.py)}")]
    p.set_cols(fy_cols)
    p.section("(b) Reconciliation of tax expense and the accounting profit multiplied by the tax rate", style="caption_bold")
    p.line("taxrec.pbt", "Profit/(loss) before tax", {k: Fx(tok("PL", "pbt", k)) for k in bp})
    p.index("taxrec.pbt")
    p.line("taxrec.rate", "Enacted income tax rate in India", dict.fromkeys(bp, 0.2517), style="pct")
    rate_row = p.row("taxrec.rate")
    pbt_row = p.row("taxrec.pbt")
    p.line("taxrec.expected", "Computed expected tax expense/(credit)", {k: Fx(f"ROUND({chr(64 + p.cols[k].col)}{pbt_row}*{chr(64 + p.cols[k].col)}{rate_row},2)") for k in bp})
    p.index("taxrec.expected")
    other = cx.precomputed.get("tax_other", {})
    p.line("taxrec.other", "Effect of unrecognised deferred tax assets, non-deductible expenses and other items (net)",
           {k: other.get(k, D(0)) for k in bp})
    p.index("taxrec.other", "note.tax.rec_other")
    total(p, "taxrec.total", "Income tax expense reported in the statement of profit and loss", ["taxrec.expected", "taxrec.other"], alias="note.tax.rec_total")
    p.section("(c) Current tax liabilities (net)", style="caption_bold")
    p.line("ctl.open", "Balance at the beginning of the year", {"CY": cx.tb("ctl.net", "PY"), "PY": cx.raw(-opening_key(cx, "ctl.net"))})
    p.index("ctl.open")
    tbline(cx, p, "ctl.current", "Current tax payable for the year", "tax.current", bp)
    p.line("ctl.paid", "Taxes paid (net of refunds)", {k: cx.raw(-cx.inp.years[k].ctl["paid"]) for k in bp})
    p.index("ctl.paid", "note.ctl.paid")
    total(p, "ctl.close", "Balance at the end of the year", ["ctl.open", "ctl.current", "ctl.paid"], alias="note.ctl.close")
    tbline(cx, p, "ctl.bs", "Current tax liabilities (net) as per balance sheet", "ctl.net", bp, alias="note.ctl.bs")
    p.text("", rid="tax.text")
    p.index("tax.text")
    p.blank()
    p.set_cols(pl_cols(cx))
    title(p, "eps")
    p.header()
    p.line("eps.pat", "Profit/(loss) attributable to equity shareholders", {k: Fx(tok("PL", "pat", k)) for k in pp})
    p.index("eps.pat")
    shares = {k: int(cx.inp.years["CY" if k in ("CY", "Q4_CY") else "PY"].shares.get("weighted" if not k.startswith("Q4") else "closing", 0)) for k in pp}
    p.line("eps.shares", "Weighted average number of equity shares (basic and diluted)", shares, style="shares")
    p.index("eps.shares", "note.eps.shares")
    srow, prow = p.row("eps.shares"), p.row("eps.pat")
    p.line("eps.value", "Basic and diluted earnings/(loss) per share (₹)", {k: Fx(f"{chr(64 + p.cols[k].col)}{prow}*{cx.div}/{chr(64 + p.cols[k].col)}{srow}") for k in pp}, style="ratio")
    p.index("eps.value", "note.eps.value")
    p.line("eps.fv", "Face value per equity share (₹)", dict.fromkeys(pp, 10), style="ratio")
    p.finish(last_col=max(c.col for c in pl_cols(cx)))
    return p


# --------------------------------------------------------------------------- Note 28 RPT


def build_rpt(cx: BuildContext) -> Page:
    ent = cx.inp.entity
    pp = cx.pl_periods
    bp = ["CY", "PY"]
    p = note_page(cx, "RPT", "rpt", pl_cols(cx))
    title(p, "rpt")
    p.section("(a) Names of related parties and nature of relationship", style="caption_bold")
    p.header("Nature of relationship", note_label="", keys=[])
    rel_col = 2
    parties: list[tuple[str, str]] = []
    if ent.holding:
        parties.append(("Holding company and ultimate holding company" if ent.ultimate_holding == ent.holding else "Holding company", ent.holding))
    for f in ent.fellow_subsidiaries:
        parties.append(("Fellow subsidiary", f))
    if ent.subsidiary:
        parties.append(("Subsidiary", ent.subsidiary))
    for d in ent.directors:
        parties.append(("Key managerial personnel", f"{d.name} – {d.role}"))
    if ent.cfo:
        parties.append(("Key managerial personnel", f"{ent.cfo.name} – {ent.cfo.role}"))
    for i, (rel, name) in enumerate(parties):
        rid = f"party.{i}"
        p.sheet.ids[rid] = p.r
        p.sheet.meta.setdefault("row_cols", {})[rid] = {"name": rel_col}
        p.put(p.r, 1, rel, "caption")
        p.put(p.r, rel_col, name, "text")
        p.r += 1
    cx.spec.entity.setdefault("rpt_parties", {name: (rel, f"party.{i}") for i, (rel, name) in enumerate(parties)})
    p.blank()
    ic = {k: cx.inp.years["CY" if k in ("CY", "Q4_CY") else "PY"].ic for k in pp}
    p.section("(b) Transactions with related parties during the year", style="caption_bold")
    p.header("Nature of transaction / related party", note_label="")
    holding, fellow_us = ent.holding or "Holding company", (ent.fellow_subsidiaries[0] if ent.fellow_subsidiaries else "Fellow subsidiary")
    fellow_uk = ent.fellow_subsidiaries[1] if len(ent.fellow_subsidiaries) > 1 else fellow_us

    def typed(nature: str) -> dict[str, object]:
        out: dict[str, object] = {}
        for k in pp:
            v = ic[k].get(nature, D(0))
            if k.startswith("Q4_"):
                out[k] = cx.raw(cx.quarter_amount(v, D("0.25")))
            else:
                out[k] = cx.raw(v)
        return out

    txns: list[tuple[str, str, str, object]] = [
        ("txn.revenue", "Revenue from operations – support services", fellow_us, "rev.related_support"),
        ("txn.support", "Support services received", holding, "oth.support_services_group"),
        ("txn.interest", "Interest on loan", holding, "fin.interest_holding"),
        ("txn.loan_received", "Loan received", holding, "loan_received"),
        ("txn.loan_repaid", "Loan repaid", holding, "loan_repaid"),
        ("txn.reimbursement", "Expenses incurred on behalf of the related party (recoverable)", fellow_uk, "reimbursement"),
        ("txn.equity", "Issue of equity shares (including securities premium)", holding, "equity_issued"),
        ("txn.investment", "Investment in equity shares of subsidiary", ent.subsidiary or "Subsidiary", "investment"),
    ]
    for rid, nature, party, src in txns:
        p.section(nature, style="caption_bold", rid=f"{rid}.head")
        if isinstance(src, str) and "." in src:
            tbline(cx, p, rid, party, src, pp, cap="caption2")
        else:
            p.line(rid, party, typed(str(src)), cap="caption2")
            p.index(rid)
        if rid == "txn.interest" and "rpt_hidden_row" in cx.inp.flags:
            row = p.r
            p.line("txn.interest.hidden", fellow_uk, {k: D("3.45") for k in pp}, cap="caption2", hidden=True)
            p.index("txn.interest.hidden")
            cx.expected_rows["rpt_hidden_row"] = (p.name, row)
    p.section("Remuneration to key managerial personnel", style="caption_bold", rid="txn.kmp.head")
    names = ", ".join(d.name for d in ent.directors) + (f" and {ent.cfo.name}" if ent.cfo else "")
    p.line("txn.kmp", f"Short-term employee benefits – {names}", {k: cx.raw(cx.inp.years["CY" if k in ("CY", "Q4_CY") else "PY"].remuneration * (D("0.25") if k.startswith("Q4") else 1)) for k in pp}, cap="caption2")
    p.index("txn.kmp")
    p.blank()
    p.set_cols(bs_cols(cx) if not cx.spfs else [ColSpec("CY", 4, f"As at\n{long_date(cx.ye)}"), ColSpec("PY", 5, f"As at\n{long_date(cx.py)}")])
    p.section("(c) Balances outstanding at the year end", style="caption_bold", rid="bal.title")
    p.header("Nature of balance / related party", note_label="")
    bals = [
        ("bal.tp", "Trade payables", holding, "tp.related"),
        ("bal.tr", "Trade receivables", fellow_us, "tr.good.related"),
        ("bal.loan", "Loan payable (including interest credited)", holding, "borr.nc.loan_holding"),
        ("bal.recoverable", "Recoverable from group companies", fellow_uk, "ofa.c.recoverable_group"),
        ("bal.investment", "Investment in subsidiary", ent.subsidiary or "Subsidiary", "inv.nc.subsidiary"),
        ("bal.equity", "Equity share capital held", holding, "eq.share_capital"),
    ]
    for rid, nature, party, key in bals:
        p.section(nature, style="caption_bold", rid=f"{rid}.head")
        tbline(cx, p, rid, party, key, bp, cap="caption2")
        if rid == "bal.tp" and "rpt_duplicate_party" in cx.inp.flags:
            row = p.r
            stale = {k: cx.tb(key, "PY") for k in bp}
            p.line("bal.tp.dup", f"{party.replace('Private Limited', 'Pvt. Ltd.')}", stale, cap="caption2")
            p.index("bal.tp.dup")
            cx.expected_rows["rpt_duplicate_party"] = (p.name, row)
    p.blank()
    p.section("(d) Terms and conditions", style="caption_bold")
    p.text(T.wording("rpt_terms")["text"], rid="rpt.terms")
    p.finish(last_col=max(c.col for c in pl_cols(cx)))
    return p


# --------------------------------------------------------------------------- Notes 29–34


RATIOS = [
    ("current_ratio", "Current ratio (times)", "Current assets", "Current liabilities", "ratio"),
    ("debt_equity", "Debt-equity ratio (times)", "Total debt (borrowings and lease liabilities)", "Total equity", "ratio"),
    ("dscr", "Debt service coverage ratio (times)", "Earnings available for debt service (profit after tax + depreciation + finance costs)", "Debt service (interest, lease payments and principal repayments)", "ratio"),
    ("roe", "Return on equity (%)", "Profit after tax", "Average total equity", "pct"),
    ("tr_turnover", "Trade receivables turnover ratio (times)", "Revenue from operations", "Average trade receivables", "ratio"),
    ("tp_turnover", "Trade payables turnover ratio (times)", "Other expenses", "Average trade payables", "ratio"),
    ("nct", "Net capital turnover ratio (times)", "Revenue from operations", "Average working capital", "ratio"),
    ("npr", "Net profit ratio (%)", "Profit after tax", "Revenue from operations", "pct"),
    ("roce", "Return on capital employed (%)", "Earnings before interest and tax", "Capital employed (total equity + total debt)", "pct"),
]


def build_note_29_34(cx: BuildContext) -> Page:
    bp = ["CY", "PY"]
    p = note_page(cx, "Note 29-34", "n2934", bs_cols(cx), landscape=True)
    title(p, "gc")
    for rid in ("gc.p1", "gc.p2", "gc.p3"):
        p.text("", rid=rid)
        p.index(rid)
    gc = T.wording("gc")
    hidden = "gc_hidden_factors" in cx.inp.flags
    for i, _f in enumerate(gc["factors"]):
        rid = f"gc.f{i}"
        p.text("", rid=rid, hidden=hidden)
        p.index(rid)
    if hidden:
        cx.expected_rows["gc_hidden_factors"] = (p.name, [p.row(f"gc.f{i}") for i in range(len(gc["factors"]))])
    p.text("", rid="gc.conclusion")
    p.index("gc.conclusion")
    p.blank()
    # Note 30 — ratios
    title(p, "ratios")
    cols = [ColSpec("num", 2, "Numerator", "text"), ColSpec("den", 3, "Denominator", "text"), ColSpec("CY", 4, "Current year"),
            ColSpec("PY", 5, "Previous year"), ColSpec("var", 6, "% variance", "pct"), ColSpec("reason", 7, "Reason for variance (where > 25%)", "text_wrap")]
    p.set_cols(cols)
    p.header("Ratio", note_label="")
    p.sheet.col_widths.update({2: 30, 3: 30, 7: 40})
    for key, label, num, den, style in RATIOS:
        rid = f"ratio.{key}"
        row = p.r
        p.line(rid, label, {"num": num, "den": den, "CY": Fx(_ratio_formula(key, "CY")), "PY": cx.precomputed.get("ratio_py", {}).get(key, D(0)),
                            "var": Fx(f"IF(E{row}=0,0,(D{row}-E{row})/ABS(E{row}))"), "reason": "-"})
        p.sheet.cells[(row, 4)].style = style
        p.sheet.cells[(row, 5)].style = style
        p.index(rid, f"note.ratio.{key}")
    p.text(T.wording("ratios_na")["text"], rid="ratio.na")
    p.blank()
    p.set_cols(bs_cols(cx))
    title(p, "contingent")
    p.header()
    p.section("(a) Contingent liabilities (claims against the Company not acknowledged as debts)", style="caption_bold")
    clines = []
    for i, label in enumerate(cx.inp.years["CY"].contingent):
        rid = f"cl.{i}"
        p.line(rid, label, {k: cx.raw(cx.inp.years[k].contingent.get(label, D(0))) for k in bp})
        clines.append(rid)
    total(p, "cl.total", "Total", clines, alias="note.contingent.total")
    p.section("(b) Commitments", style="caption_bold")
    p.line("commit", "Estimated amount of contracts remaining to be executed on capital account (net of advances)", {k: cx.raw(cx.inp.years[k].commitments) for k in bp})
    p.blank()
    title(p, "fi")
    p.header()
    p.section("(a) Categories of financial instruments – measured at amortised cost", style="caption_bold")
    p.section("Financial assets", style="caption")
    p.line("fi.tr", "Trade receivables", {k: Fx(tok("Note 6-11", "tr.net", k)) for k in bp})
    p.line("fi.cash", "Cash and cash equivalents", {k: Fx(tok("Note 6-11", "cash.total", k)) for k in bp})
    p.line("fi.ofa", "Other financial assets (non-current and current)", {k: Fx(tok("Note 6-11", "ofa.nc.total", k) + "+" + tok("Note 6-11", "ofa.c.total", k)) for k in bp})
    total(p, "fi.assets", "Total financial assets", ["fi.tr", "fi.cash", "fi.ofa"], alias="note.fi.assets")
    p.section("Financial liabilities", style="caption")
    p.line("fi.borr", "Borrowings (non-current and current)", {k: Fx(tok("Note 14-19", "borr.nc.total", k) + "+" + tok("Note 14-19", "borr.c.total", k)) for k in bp})
    p.index("fi.borr", "note.fi.borrowings")
    p.line("fi.lease", "Lease liabilities", {k: Fx(tok("Note 14-19", "lease.split", k)) for k in bp})
    p.line("fi.tp", "Trade payables", {k: Fx(tok("Note 14-19", "tp.total", k)) for k in bp})
    p.line("fi.ofl", "Other financial liabilities", {k: Fx(tok("Note 14-19", "ofl.total", k)) for k in bp})
    total(p, "fi.liabilities", "Total financial liabilities", ["fi.borr", "fi.lease", "fi.tp", "fi.ofl"], alias="note.fi.liabilities")
    p.text(T.wording("investments_scope")["text"], rid="fi.scope")
    p.text(T.wording("fair_value")["text"], rid="fi.fv")
    p.blank()
    title(p, "capital")
    p.header()
    p.line("cap.debt", "Total borrowings (including lease liabilities)", {k: Fx(tok("Note 14-19", "borr.nc.total", k) + "+" + tok("Note 14-19", "borr.c.total", k) + "+" + tok("Note 14-19", "lease.split", k)) for k in bp})
    p.line("cap.cash", "Less: Cash and cash equivalents", {k: Fx("-" + tok("Note 6-11", "cash.total", k)) for k in bp})
    total(p, "cap.net_debt", "Net debt", ["cap.debt", "cap.cash"], alias="note.capital.net_debt")
    p.line("cap.equity", "Total equity", {k: Fx(tok("BS", "eq.total", k)) for k in bp})
    nd, eq = p.row("cap.net_debt"), p.row("cap.equity")
    p.line("cap.ratio", "Net debt to equity ratio (times)", {k: Fx(f"{chr(64 + p.cols[k].col)}{nd}/{chr(64 + p.cols[k].col)}{eq}") for k in bp}, style="ratio")
    p.blank()
    title(p, "events")
    ev = T.wording("events")
    p.text(ev["p1"], rid="events.p1")
    p.text(T.fill(ev["p2"], sign_date=long_date(cx.inp.entity.signing_date)), rid="events.p2")
    p.index("events.p2")
    p.blank()
    return p


def _ratio_formula(key: str, k: str) -> str:
    def bs(rid: str, kk: str = k) -> str:
        return tok("BS", rid, kk)

    def pl(rid: str) -> str:
        return tok("PL", rid, k)

    def cf(rid: str) -> str:
        return tok("CFS", rid, k)

    debt = f"({bs('borr.nc')}+{bs('borr.c')}+{bs('lease.nc')}+{bs('lease.c')})"
    if key == "current_ratio":
        return f"{bs('ca.total')}/{bs('cl.total')}"
    if key == "debt_equity":
        return f"{debt}/{bs('eq.total')}"
    if key == "dscr":
        return f"({pl('pat')}+{pl('dep')}+{pl('fin')})/-({cf('interest_paid')}+{cf('lease_principal')}+{cf('lease_interest')}+{cf('borrow_out')})"
    if key == "roe":
        return f"{pl('pat')}/(({bs('eq.total', 'CY')}+{bs('eq.total', 'PY')})/2)"
    if key == "tr_turnover":
        return f"{pl('rev')}/(({bs('tr', 'CY')}+{bs('tr', 'PY')})/2)"
    if key == "tp_turnover":
        return f"{pl('oth')}/(({bs('tp.msme', 'CY')}+{bs('tp.others', 'CY')}+{bs('tp.msme', 'PY')}+{bs('tp.others', 'PY')})/2)"
    if key == "nct":
        return f"{pl('rev')}/((({bs('ca.total', 'CY')}-{bs('cl.total', 'CY')})+({bs('ca.total', 'PY')}-{bs('cl.total', 'PY')}))/2)"
    if key == "npr":
        return f"{pl('pat')}/{pl('rev')}"
    if key == "roce":
        return f"({pl('pbt')}+{pl('fin')})/({bs('eq.total')}+{debt})"
    raise KeyError(key)
