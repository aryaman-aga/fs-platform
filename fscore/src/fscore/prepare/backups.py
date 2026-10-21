"""Backup sheets: the NetSuite-style trial balance, the fixed-asset register and the ageing schedules.

Backups hold amounts in rupees; print sheets link to them (02 §2.1).
"""

from __future__ import annotations

from decimal import Decimal

from fscore.prepare.context import TB_FIRST_ROW, TB_SHEET, BuildContext, long_date
from fscore.prepare.layout import ColSpec, Fx, Page

D = Decimal
CLASS_LABELS = {"computers": "Computers", "furniture": "Furniture and fixtures", "office_equipment": "Office equipment"}


def build_tb(cx: BuildContext) -> Page:
    inp = cx.inp
    cols = [ColSpec("CY", 9, inp.tb.period_labels.get("CY", "CY"), "tb_amount"), ColSpec("PY", 10, inp.tb.period_labels.get("PY", "PY"), "tb_amount")]
    cx.tb_cols = {"CY": "I", "PY": "J"}
    if cx.spfs:
        cols += [ColSpec("Q4_CY", 11, "Qtr " + inp.tb.period_labels.get("CY", "CY"), "tb_amount"), ColSpec("Q4_PY", 12, "Qtr " + inp.tb.period_labels.get("PY", "PY"), "tb_amount")]
        cols += [ColSpec("FC_CY", 13, f"{inp.tb.period_labels.get('CY', 'CY')} ({inp.entity.functional_currency})", "tb_amount"),
                 ColSpec("FC_PY", 14, f"{inp.tb.period_labels.get('PY', 'PY')} ({inp.entity.functional_currency})", "tb_amount")]
        cx.tb_cols.update({"Q4_CY": "K", "Q4_PY": "L", "FC_CY": "M", "FC_PY": "N"})
    p = Page(cx.spec, TB_SHEET, "tb", cols, code="tb", caption_width=12, amount_width=16, selected=False)
    sh = p.sheet
    sh.col_widths.update({1: 10, 2: 44, 3: 14, 4: 20, 5: 26, 6: 26, 7: 30, 8: 30})
    sh.set(1, 1, inp.entity.name, style="tb_header")
    sh.set(2, 1, "Trial balance – saved search FT27 (amounts in INR)", style="tb_text")
    heads = ["Account No", "Account Name", "Level 1", "Level 2", "Level 3", "Level 4", "Level 5", "FS Grouping"]
    for i, h in enumerate(heads, start=1):
        sh.set(4, i, h, style="tb_header")
    for c in cols:
        sh.set(4, c.col, c.header, style="tb_header")
    r = TB_FIRST_ROW
    for a in inp.tb.accounts:
        sh.set(r, 1, a.code, style="tb_text")
        sh.set(r, 2, a.name, style="tb_text")
        for i, lv in enumerate(a.levels[:5], start=3):
            sh.set(r, i, lv, style="tb_text")
        sh.set(r, 8, a.key, style="tb_text")
        is_pl = a.key.split(".")[0] in ("rev", "oi", "emp", "fin", "dep", "oth", "tax", "oci")
        sh.set(r, 9, float(a.amount("CY")), style="tb_amount")
        sh.set(r, 10, float(a.amount("PY")), style="tb_amount")
        if cx.spfs:
            if is_pl:
                for k, col in (("Q4_CY", 11), ("Q4_PY", 12)):
                    fy = a.amount("CY" if k == "Q4_CY" else "PY")
                    sh.set(r, col, float(cx.amount_for(a.code, fy, k)), style="tb_amount")
            rates = inp.fx
            for _k, col, per in (("FC_CY", 13, "CY"), ("FC_PY", 14, "PY")):
                rate = rates.get(per, {}).get("average" if is_pl else "closing", D(1))
                sh.set(r, col, float((a.amount(per) / rate).quantize(D("0.01"))), style="tb_amount")
        r += 1
    cx.tb_last_row = r - 1
    sh.set(r, 2, "Total", style="tb_header")
    for c in cols:
        letter = cx.col_letter(c.col)
        sh.set(r, c.col, formula=f"SUM({letter}{TB_FIRST_ROW}:{letter}{r - 1})", style="tb_amount")
    sh.ids["total"] = r
    p.r = r + 1
    sh.print_area = None
    sh.selected = False
    return p


def build_far(cx: BuildContext) -> Page:
    """Fixed-asset register summary (rupees) — the PPE / ROU / software notes link here."""
    cols = [
        ColSpec("open", 2, "Opening gross block", "tb_amount"), ColSpec("add", 3, "Additions (incl. CWIP capitalised)", "tb_amount"),
        ColSpec("disp", 4, "Disposals", "tb_amount"), ColSpec("close", 5, "Closing gross block", "tb_amount"),
        ColSpec("dopen", 6, "Opening accumulated depreciation", "tb_amount"), ColSpec("charge", 7, "Charge for the year", "tb_amount"),
        ColSpec("ddisp", 8, "Depreciation on disposals", "tb_amount"), ColSpec("dclose", 9, "Closing accumulated depreciation", "tb_amount"),
        ColSpec("net", 10, "Net block", "tb_amount"),
    ]
    p = Page(cx.spec, "FAR", "backup", cols, code="far", caption_width=34, amount_width=16, selected=False)
    sh = p.sheet
    sh.set(1, 1, cx.inp.entity.name, style="tb_header")
    sh.set(2, 1, "Fixed asset register – summary by class (amounts in INR)", style="tb_text")
    p.r = 4
    p.header("Asset class", note_label="")
    years = [("PY", cx.inp.years["PY"]), ("CY", cx.inp.years["CY"])]
    for yk, yd in years:
        p.section(f"Financial year ended {long_date(cx.ye if yk == 'CY' else cx.py)}", style="tb_header")
        members = []
        for cls, roll in yd.far.items():
            rid = f"{yk}.{cls}"
            members.append(rid)
            row = p.r
            opening: object = float(roll.opening)
            dopening: object = float(roll.dep_opening)
            if yk == "CY":
                opening = Fx(p.ref(f"PY.{cls}", "close"))
                dopening = Fx(p.ref(f"PY.{cls}", "dclose"))
            p.line(rid, CLASS_LABELS.get(cls, cls), {
                "open": opening, "add": float(roll.additions), "disp": float(roll.disposals),
                "close": Fx(f"B{row}+C{row}-D{row}"), "dopen": dopening, "charge": float(roll.dep_charge),
                "ddisp": float(roll.dep_disposals), "dclose": Fx(f"F{row}+G{row}-H{row}"), "net": Fx(f"E{row}-I{row}"),
            }, cap="tb_text", style="tb_amount")
        p.total(f"{yk}.total", "Total", members, style="tb_amount", cap="tb_header")
        p.blank()
    for label, attr in (("Right-of-use assets – buildings", "rou"), ("Computer software", "software")):
        p.section(label, style="tb_header")
        for yk, yd in years:
            roll = getattr(yd, attr)
            row = p.r
            rid = f"{attr}.{yk}"
            opening = float(roll.opening) if yk == "PY" else Fx(p.ref(f"{attr}.PY", "close"))
            dopening = float(roll.dep_opening) if yk == "PY" else Fx(p.ref(f"{attr}.PY", "dclose"))
            p.line(rid, f"FY ended {long_date(cx.ye if yk == 'CY' else cx.py)}", {
                "open": opening, "add": float(roll.additions), "disp": float(roll.disposals), "close": Fx(f"B{row}+C{row}-D{row}"),
                "dopen": dopening, "charge": float(roll.dep_charge), "ddisp": float(roll.dep_disposals),
                "dclose": Fx(f"F{row}+G{row}-H{row}"), "net": Fx(f"E{row}-I{row}"),
            }, cap="tb_text", style="tb_amount")
        p.blank()
    p.section("Capital work-in-progress (opening / additions / capitalised / closing)", style="tb_header")
    for yk, yd in years:
        roll = yd.cwip
        row = p.r
        opening = float(roll.opening) if yk == "PY" else Fx(p.ref("cwip.PY", "close"))
        p.line(f"cwip.{yk}", f"FY ended {long_date(cx.ye if yk == 'CY' else cx.py)}", {
            "open": opening, "add": float(roll.additions), "disp": float(roll.transfers), "close": Fx(f"B{row}+C{row}-D{row}"),
        }, cap="tb_text", style="tb_amount")
    sh.selected = False
    return p


def ageing_split(cx: BuildContext, yk: str) -> dict[str, dict[str, Decimal]]:
    """Receivable ageing (rupees): considered good by bucket; credit impaired in the oldest bucket."""
    tb = cx.inp.tb
    good = tb.by_key("tr.good.third_party", yk) + tb.by_key("tr.good.related", yk)
    impaired = tb.by_key("tr.impaired", yk)
    buckets = ["Not due", "Less than 6 months", "6 months - 1 year", "1-2 years", "2-3 years", "More than 3 years"]
    q = D("0.01") if cx.inp.exact else D(cx.div) / 100
    shares = [D("0.52"), D("0.31"), D("0.09"), D("0.05"), D("0.02")]
    good_b: dict[str, Decimal] = {}
    rest = good
    for b, s in zip(buckets[:-1], shares, strict=True):
        good_b[b] = (good * s / q).quantize(D(1)) * q
        rest -= good_b[b]
    good_b[buckets[-1]] = rest
    imp_b = {b: D(0) for b in buckets}
    imp_b["More than 3 years"] = impaired
    return {"good": good_b, "impaired": imp_b}


def build_ageing(cx: BuildContext) -> Page:
    rb = ["Not due", "Less than 6 months", "6 months - 1 year", "1-2 years", "2-3 years", "More than 3 years"]
    cols = [ColSpec(b, i + 2, b, "tb_amount") for i, b in enumerate(rb)] + [ColSpec("total", 8, "Total", "tb_amount")]
    p = Page(cx.spec, "Ageing", "backup", cols, code="ageing", caption_width=36, amount_width=16, selected=False)
    p.sheet.set(1, 1, cx.inp.entity.name, style="tb_header")
    p.sheet.set(2, 1, "Receivables and payables ageing (amounts in INR)", style="tb_text")
    p.r = 4
    p.header("Trade receivables", note_label="")
    for yk in ("CY", "PY"):
        split = ageing_split(cx, yk)
        p.section(f"As at {long_date(cx.ye if yk == 'CY' else cx.py)}", style="tb_header")
        for kind, label in (("good", "Undisputed – considered good"), ("impaired", "Undisputed – credit impaired")):
            row = p.r
            vals: dict[str, object] = {b: float(split[kind][b]) for b in rb}
            vals["total"] = Fx(f"SUM(B{row}:G{row})")
            p.line(f"tr.{yk}.{kind}", label, vals, cap="tb_text", style="tb_amount")
        p.total(f"tr.{yk}.total", "Total", [f"tr.{yk}.good", f"tr.{yk}.impaired"], style="tb_amount", cap="tb_header")
        p.blank()
    pb = ["Not due", "Less than 1 year", "1-2 years", "2-3 years", "More than 3 years"]
    p.set_cols([ColSpec(b, i + 2, b, "tb_amount") for i, b in enumerate(pb)] + [ColSpec("total", 7, "Total", "tb_amount")])
    p.header("Trade payables", note_label="")
    for yk in ("CY", "PY"):
        ageing = cx.inp.years[yk].payable_ageing
        p.section(f"As at {long_date(cx.ye if yk == 'CY' else cx.py)}", style="tb_header")
        for kind in ("MSME", "Others"):
            row = p.r
            vals = {b: float(ageing.get(kind, {}).get(b, D(0))) for b in pb}
            vals["total"] = Fx(f"SUM(B{row}:F{row})")
            p.line(f"tp.{yk}.{kind}", kind, vals, cap="tb_text", style="tb_amount")
        p.total(f"tp.{yk}.total", "Total", [f"tp.{yk}.MSME", f"tp.{yk}.Others"], style="tb_amount", cap="tb_header")
        p.blank()
    p.sheet.selected = False
    return p
