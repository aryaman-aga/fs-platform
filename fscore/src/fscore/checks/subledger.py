"""Supporting-schedule family (02 §6.4): SUB-01…05 and SUB-08.

Receivables / payables ageing vs the notes, MSME disclosure, employee-benefit
(actuarial) figures, leases, Ind AS 115 disaggregation bases and the
completeness of financial-instrument tables. Share capital (SUB-06/07) lives in
``share_capital.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from rapidfuzz import fuzz

from fscore.checks.base import CheckContext, is_total_caption
from fscore.checks.xref import (
    Loc,
    amt,
    caption_rows,
    ev,
    find_note,
    has_number,
    line_loc,
    loc,
    periods_at,
    row_numbers,
    sums_rounding_only,
)
from fscore.engine.periods import PeriodLabel, parse_period, scope_key
from fscore.engine.statements import NoteLoc, norm_caption
from fscore.engine.tables import Table
from fscore.io.xlsx_reader import Sheet
from fscore.models import Certainty, Evidence
from fscore.registry import check

ZERO = Decimal(0)
_BUCKET = re.compile(r"less\s+than|more\s+than|\d+\s*[-–]\s*\d+\s*(years?|months?|yrs?)|months?\s*[-–]\s*\d+\s*years?|not\s+due|^\s*0\s*[-–]|\bdays?\b")


def _emit(ctx: CheckContext, rule: str, x: Loc, expected: Decimal | None, *, what: str, evidence: list[Evidence], fix: str,
          key: str, period: str | None = None, certainty: Certainty | None = None,
          x_parts: list[tuple[Loc, int]] | None = None, parts: list[tuple[Loc, int]] | None = None, absolute: bool = True) -> None:
    if parts and sums_rounding_only(ctx, x_parts or [(x, 1)], parts, absolute=absolute):
        return  # the same figure through different rounding paths (or an approved balancing adjustment)
    sh = ctx.wb.sheet(x.sheet)
    ctx.emit(
        rule, sheet=x.sheet, cells=[x.addr], caption=ctx.caption(sh, x.row), printed=ctx.printed_text(sh, x.row, x.col),
        expected=amt(expected) if expected is not None else None,
        difference=(x.value - expected) if (x.value is not None and expected is not None) else None,
        explanation=what, evidence=evidence, suggested_fix=fix, key=key, period=period, certainty=certainty,
    )


def _note_rows(ctx: CheckContext, note: NoteLoc, rx: re.Pattern[str], *, start: int | None = None, end: int | None = None) -> list[int]:
    sh = ctx.wb.sheet(note.sheet)
    return [r for r in caption_rows(ctx, sh, rx, start or note.row, end or note.end_row or sh.max_row) if row_numbers(ctx, sh, r) and not sh.row_hidden(r)]


def _val(ctx: CheckContext, sh: Sheet, row: int, period: str) -> Loc | None:
    col = periods_at(ctx, sh, row).get(period)
    if col is None or not has_number(ctx, sh, row, col):
        return None
    return loc(ctx, sh, row, col)


def _table_period(ctx: CheckContext, sh: Sheet, t: Table, max_up: int = 3) -> str | None:
    """Period of a table whose columns are not periods: a short heading just above it, or its title."""
    top = (t.header_rows[0] if t.header_rows else t.r1) - 1
    texts = [t.title or ""]
    for r in range(top, max(0, top - max_up), -1):
        texts.append(ctx.caption(sh, r))
    for txt in texts:
        if not txt or len(txt) > 100:
            continue  # narrative paragraphs quote other dates
        lab = parse_period(txt)
        if lab is not None and lab.end is not None:
            k = scope_key(PeriodLabel(lab.end, None, "point", ""), ctx.config)
            if k:
                return k
    return None


@dataclass
class Block:
    sheet: Sheet
    table: Table
    period: str
    rows: list[int]


def _blocks(ctx: CheckContext, sh: Sheet, t: Table) -> list[Block]:
    """Split a table into period blocks headed "As at March 31, 2026" / "As at March 31, 2025"."""
    out: list[Block] = []
    current: Block | None = None
    start = (t.header_rows[-1] + 1) if t.header_rows else t.r1
    for r in range(start, t.r2 + 1):
        if sh.row_hidden(r):
            continue
        if r in t.rows:
            if current is None:
                p = _table_period(ctx, sh, t)
                if p is None:
                    continue
                current = Block(sh, t, p, [])
                out.append(current)
            current.rows.append(r)
            continue
        cap = ctx.caption(sh, r)
        if cap and len(cap) < 80:
            lab = parse_period(cap)
            if lab is not None and lab.end is not None:
                p = scope_key(PeriodLabel(lab.end, None, "point", ""), ctx.config)
                current = Block(sh, t, p, []) if p else None
                if current is not None:
                    out.append(current)
    return [b for b in out if b.rows]


def _ageing_blocks(ctx: CheckContext, kind: str) -> list[Block]:
    """Receivable / payable ageing schedules on print sheets, split by period."""
    out: list[Block] = []
    for sh in ctx.print_sheets:
        for t in ctx.tables(sh):
            buckets = [c for c in t.num_cols if _BUCKET.search((t.headers.get(c) or "").lower())]
            if len(buckets) < 3:
                continue
            ctxt = " ".join([t.title or "", *(ctx.caption(sh, r) for r in range(max(1, t.r1 - 4), t.r2 + 1))]).lower()
            is_pay = bool(re.search(r"payable|msme|micro\s+(enterprises\s+)?(and|&)\s+small|creditor", ctxt))
            is_rec = bool(re.search(r"receivable|considered\s+good|credit\s+impaired|debtor", ctxt))
            if kind == "payables" and not is_pay:
                continue
            if kind == "receivables" and (not is_rec or "payable" in (t.title or "").lower()):
                continue
            out.extend(_blocks(ctx, sh, t))
    return out


def _total_col(t: Table) -> int | None:
    return next((c for c in reversed(t.num_cols) if re.search(r"\btotal\b", (t.headers.get(c) or "").lower())), None)


Parts = list[tuple[Loc, int]]


def _grand_total(ctx: CheckContext, b: Block) -> tuple[Loc, Parts] | None:
    """The block's grand total and the printed cells it consists of."""
    sh, t = b.sheet, b.table
    tc = _total_col(t)
    total_row = next((r for r in reversed(b.rows) if is_total_caption(ctx.caption(sh, r))), None)
    if tc is not None and total_row is not None and has_number(ctx, sh, total_row, tc):
        x = loc(ctx, sh, total_row, tc)
        return x, [(x, 1)]
    if tc is not None:
        parts = [(loc(ctx, sh, r, tc), 1) for r in b.rows if has_number(ctx, sh, r, tc)]
        return Loc(sh.name, b.rows[-1], tc, sum((q.value or ZERO for q, _s in parts), ZERO)), parts
    return None


def _row_total(ctx: CheckContext, b: Block, rx: re.Pattern[str]) -> tuple[Loc, Parts] | None:
    sh, t = b.sheet, b.table
    tc = _total_col(t)
    rows = [r for r in b.rows if rx.search(norm_caption(ctx.caption(sh, r)))]
    if not rows or tc is None:
        return None
    parts = [(loc(ctx, sh, r, tc), 1) for r in rows]
    return Loc(sh.name, rows[0], tc, sum((q.value or ZERO for q, _s in parts), ZERO)), parts


def _sum_loc(locs: list[Loc]) -> tuple[Loc, Parts]:
    return Loc(locs[0].sheet, locs[0].row, locs[0].col, sum((x.value or ZERO for x in locs), ZERO)), [(x, 1) for x in locs]


# =============================================================================== SUB-01 / SUB-02


@check("SUB-01")
def check_receivables_ageing(ctx: CheckContext) -> None:
    note = find_note(ctx, re.compile(r"trade\s+receivables"))
    if note is None:
        return
    nsh = ctx.wb.sheet(note.sheet)
    end = note.end_row or nsh.max_row
    blocks = _ageing_blocks(ctx, "receivables")
    ageing_top = min((b.table.r1 for b in blocks if b.sheet.name == nsh.name and note.row <= b.table.r1 <= end), default=end + 1)
    last = ageing_top - 1  # the note's own lines sit above its ageing schedule
    good_rows = _note_rows(ctx, note, re.compile(r"considered\s+good"), end=last)
    imp_rows = _note_rows(ctx, note, re.compile(r"credit\s+impaired|doubtful"), end=last)
    allow_rows = _note_rows(ctx, note, re.compile(r"allowance|provision\s+for|expected\s+credit\s+loss|impairment\s+allowance"), end=last)
    tot_rows = [r for r in _note_rows(ctx, note, re.compile(r"^total|^gross"), end=last) if not allow_rows or r < allow_rows[0]]
    net_rows = [r for r in _note_rows(ctx, note, re.compile(r"^total|^net"), end=last) if allow_rows and r > allow_rows[0]]
    for b in blocks:
        p = b.period
        gt = _grand_total(ctx, b)
        if gt is None or gt[0].value is None:
            continue
        g, g_parts = gt
        targets: list[tuple[Loc, Parts]] = []
        if tot_rows and (x := _val(ctx, nsh, tot_rows[0], p)) is not None:
            targets.append((x, [(x, 1)]))
        elif good_rows:
            vals = [x for x in (_val(ctx, nsh, r, p) for r in good_rows + imp_rows) if x is not None]
            if vals:
                targets.append(_sum_loc(vals))
        if net_rows and (x := _val(ctx, nsh, net_rows[-1], p)) is not None:
            targets.append((x, [(x, 1)]))
        targets = [t for t in targets if t[0].value is not None]
        if targets and not any(abs(g.value) == abs(t[0].value) for t in targets):  # type: ignore[arg-type]
            want, want_parts = targets[0]
            _emit(ctx, "SUB-01", g, want.value,
                  what=f"Receivables ageing ({p}) totals {amt(g.value)} but the trade receivables note shows gross {amt(want.value)}.",
                  evidence=[ev(ctx, g, "Ageing total"), ev(ctx, want, "Note gross")],
                  fix="Reconcile the ageing schedule with the receivables ledger and the note (gross of the allowance).", key=f"ageing:{p}", period=p,
                  x_parts=g_parts, parts=want_parts)
            continue
        for rx, rows, label in ((re.compile(r"considered\s+good"), good_rows, "considered good"),
                                (re.compile(r"credit\s+impaired|doubtful"), imp_rows, "credit impaired"),
                                (re.compile(r"allowance|provision|expected\s+credit"), allow_rows, "allowance")):
            rt = _row_total(ctx, b, rx)
            vals = [v for v in (_val(ctx, nsh, r, p) for r in rows) if v is not None]
            if rt is None or not vals:
                continue
            a, a_parts = rt
            n, n_parts = _sum_loc(vals)
            if abs(a.value or ZERO) != abs(n.value or ZERO):
                _emit(ctx, "SUB-01", a, n.value, what=f"Ageing '{label}' ({p}) totals {amt(a.value)} but the note shows {amt(n.value)}.",
                      evidence=[ev(ctx, a, "Ageing")] + [ev(ctx, v, "Note") for v in vals],
                      fix=f"Align the ageing '{label}' rows with the note.", key=f"ageing:{label}:{p}", period=p, x_parts=a_parts, parts=n_parts)


_MSME = re.compile(r"micro\s+(enterprises\s+)?(and|&)\s+small|msme|\bmse\b")


@check("SUB-02")
def check_payables_ageing(ctx: CheckContext) -> None:
    note = find_note(ctx, re.compile(r"trade\s+payables"))
    if note is None:
        return
    nsh = ctx.wb.sheet(note.sheet)
    end = note.end_row or nsh.max_row
    blocks = _ageing_blocks(ctx, "payables")
    ageing_top = min((b.table.r1 for b in blocks if b.sheet.name == nsh.name and note.row <= b.table.r1 <= end), default=end + 1)
    msme_rows = [r for r in _note_rows(ctx, note, _MSME, end=ageing_top - 1) if not re.search(r"interest|principal", norm_caption(ctx.caption(nsh, r)))]
    tot_rows = [r for r in _note_rows(ctx, note, re.compile(r"^total"), end=ageing_top - 1) if "outstanding dues" not in norm_caption(ctx.caption(nsh, r))]
    for b in blocks:
        p = b.period
        gt = _grand_total(ctx, b)
        if gt is None or gt[0].value is None or not tot_rows:
            continue
        g, g_parts = gt
        note_tot = _val(ctx, nsh, tot_rows[0], p)
        if note_tot is not None and note_tot.value is not None and abs(note_tot.value) != abs(g.value):  # type: ignore[arg-type]
            _emit(ctx, "SUB-02", g, note_tot.value, what=f"Payables ageing ({p}) totals {amt(g.value)} but the trade payables note shows {amt(note_tot.value)}.",
                  evidence=[ev(ctx, g, "Ageing total"), ev(ctx, note_tot, "Note total")], fix="Reconcile the payables ageing with the note.", key=f"ageing:{p}", period=p,
                  x_parts=g_parts, parts=[(note_tot, 1)])
            continue
        rt = _row_total(ctx, b, _MSME)
        if rt is not None and msme_rows:
            a, a_parts = rt
            m = _val(ctx, nsh, msme_rows[0], p)
            if m is not None and m.value is not None and abs(m.value) != abs(a.value or ZERO):
                _emit(ctx, "SUB-02", a, m.value, what=f"Ageing MSME dues ({p}) total {amt(a.value)} but the note shows {amt(m.value)}.",
                      evidence=[ev(ctx, a, "Ageing MSME"), ev(ctx, m, "Note MSME")], fix="Align the MSME ageing with the note.", key=f"ageing:msme:{p}", period=p,
                      x_parts=a_parts, parts=[(m, 1)])
    # MSMED Act disclosure: principal amount unpaid = MSME dues in the note
    if not msme_rows:
        return
    for sh in ctx.print_sheets:
        for r in caption_rows(ctx, sh, re.compile(r"principal\s+amount.*(unpaid|due|remaining)")):
            for p in periods_at(ctx, sh, r):
                x = _val(ctx, sh, r, p)
                m = _val(ctx, nsh, msme_rows[0], p)
                if x is None or m is None or x.value is None or m.value is None or x.value == m.value:
                    continue
                _emit(ctx, "SUB-02", x, m.value, what=f"MSMED Act disclosure: principal amount unpaid ({p}) {amt(x.value)} differs from MSME dues in the trade payables note {amt(m.value)}.",
                      evidence=[ev(ctx, x, "MSME disclosure"), ev(ctx, m, "Trade payables note")], fix="Use the MSME dues per the note in the MSMED disclosure.", key=f"msme:{r}:{p}", period=p,
                      parts=[(m, 1)])


# =============================================================================== SUB-03 employee benefits

_DBO_CLOSE = re.compile(r"obligation.*(end|closing)|closing\s+(defined\s+benefit\s+)?obligation|net\s+(defined\s+benefit\s+)?liability|present\s+value\s+of.*obligation\s*$|liability\s+recogni[sz]ed")


_SPLIT_NC = re.compile(r"^non[\s-]*current(\s+(liability|liabilities|portion|provision|provisions))?\b\s*(\(.*\))?$")
_SPLIT_C = re.compile(r"^current(\s+(liability|liabilities|portion|provision|provisions))?\b\s*(\(.*\))?$")


@check("SUB-03")
def check_employee_benefits(ctx: CheckContext) -> None:
    for sh in ctx.print_sheets:
        for r in range(1, sh.max_row + 1):
            cap = norm_caption(ctx.caption(sh, r))
            if not _SPLIT_NC.match(cap) or not row_numbers(ctx, sh, r):
                continue
            cur = next((rr for rr in range(r - 3, r + 4) if rr != r and _SPLIT_C.match(norm_caption(ctx.caption(sh, rr))) and row_numbers(ctx, sh, rr)), None)
            if cur is None:
                continue
            context = " ".join(norm_caption(ctx.caption(sh, rr)) for rr in range(max(1, min(r, cur) - 25), min(r, cur)))
            if not re.search(r"gratuity|defined\s+benefit|obligation|actuar", context):
                continue
            dbo_row = next((rr for rr in range(min(r, cur) - 1, max(0, min(r, cur) - 25), -1)
                            if _DBO_CLOSE.search(norm_caption(ctx.caption(sh, rr))) and row_numbers(ctx, sh, rr)), None)
            if dbo_row is None:
                continue
            for p, col in periods_at(ctx, sh, r).items():
                a, b, d = (_val(ctx, sh, x, p) for x in (cur, r, dbo_row))
                if a is None or b is None or d is None or a.value is None or b.value is None or d.value is None:
                    continue
                split = a.value + b.value
                tot = next((rr for rr in range(max(r, cur) + 1, max(r, cur) + 3) if is_total_caption(ctx.caption(sh, rr)) and has_number(ctx, sh, rr, col)), None)
                anchor = loc(ctx, sh, tot, col) if tot else b
                if tot is not None:
                    if anchor.value == d.value or sums_rounding_only(ctx, [(anchor, 1)], [(d, 1)]):
                        continue  # the printed split total agrees; portion rounding is a casting matter (CAST-01)
                    split = anchor.value if anchor.value is not None else split
                elif abs(split - d.value) <= Decimal("0.01"):
                    continue  # two separately rounded portions
                ctx.emit(
                    "SUB-03", sheet=sh.name, cells=[anchor.addr, d.addr], caption=ctx.caption(sh, anchor.row), printed=ctx.printed_text(sh, anchor.row, col),
                    expected=amt(d.value), difference=split - d.value,
                    explanation=f"Current {amt(a.value)} + non-current {amt(b.value)} = {amt(split)} but the defined-benefit obligation is {amt(d.value)} ({p}).",
                    evidence=[ev(ctx, a, "Current"), ev(ctx, b, "Non-current"), ev(ctx, d, "Obligation")],
                    suggested_fix="Take the current / non-current split from the actuarial report so it adds to the obligation (non-current = obligation − current).",
                    key=f"split:{r}:{p}", period=p,
                )
    _expense_vs_actuarial(ctx)


def _expense_vs_actuarial(ctx: CheckContext) -> None:
    """Expense recognised per the actuarial disclosure = gratuity expense in the employee benefits note."""
    emp = find_note(ctx, re.compile(r"employee\s+benefits?\s+expense"))
    if emp is None:
        return
    esh = ctx.wb.sheet(emp.sheet)
    grat = _note_rows(ctx, emp, re.compile(r"gratuity|defined\s+benefit"))
    if not grat:
        return
    for sh in ctx.print_sheets:
        for sc_row in caption_rows(ctx, sh, re.compile(r"^current\s+service\s+cost")):
            if not row_numbers(ctx, sh, sc_row):
                continue
            # the "expense recognised" table: service cost, interest cost, then its total
            ic_row = next((r for r in range(sc_row + 1, sc_row + 4) if re.search(r"^(net\s+)?interest\s+(cost|expense)", norm_caption(ctx.caption(sh, r)))), None)
            tot_row = next((r for r in range(sc_row + 1, sc_row + 6) if re.search(r"^total\s+(expense|cost|amount)|^total$", norm_caption(ctx.caption(sh, r)))
                            and row_numbers(ctx, sh, r)), None)
            if tot_row is None:
                continue  # the obligation roll-forward (no total expense line) is checked by ROLL-01
            for p in periods_at(ctx, esh, grat[0]):
                g = _val(ctx, esh, grat[0], p)
                t = _val(ctx, sh, tot_row, p)
                s_ = _val(ctx, sh, sc_row, p)
                if g is None or t is None or s_ is None or g.value is None or t.value is None:
                    continue
                if g.value in (t.value, s_.value) or sums_rounding_only(ctx, [(t, 1)], [(g, 1)]) or sums_rounding_only(ctx, [(s_, 1)], [(g, 1)]):
                    continue
                ic = _val(ctx, sh, ic_row, p) if ic_row else None
                ctx.emit(
                    "SUB-03", sheet=sh.name, cells=[t.addr], caption=ctx.caption(sh, tot_row), printed=ctx.printed_text(sh, tot_row, t.col),
                    expected=amt(g.value), difference=t.value - g.value,
                    explanation=(f"Gratuity expense per the actuarial disclosure ({p}) {amt(t.value)} (service cost {amt(s_.value)}"
                                 + (f" + interest cost {amt(ic.value)}" if ic is not None else "") + f") differs from the employee benefits expense note {amt(g.value)}."),
                    evidence=[ev(ctx, t, "Actuarial expense"), ev(ctx, s_, "Current service cost"), ev(ctx, g, "Employee benefits note")],
                    suggested_fix="Book the gratuity expense per the actuarial report (service cost, and net interest where presented in employee cost).",
                    key=f"expense:{p}", period=p,
                )


# =============================================================================== SUB-04 leases


@check("SUB-04")
def check_leases(ctx: CheckContext) -> None:
    lease = find_note(ctx, re.compile(r"lease\s+liabilit"))
    rou = find_note(ctx, re.compile(r"right[\s-]+of[\s-]+use"))
    fin = find_note(ctx, re.compile(r"finance\s+costs?"))
    if lease is None:
        return
    lsh = ctx.wb.sheet(lease.sheet)
    l_add = _note_rows(ctx, lease, re.compile(r"addition|new\s+leases"))
    l_int = _note_rows(ctx, lease, re.compile(r"accretion|interest|finance\s+cost"))
    l_pay = _note_rows(ctx, lease, re.compile(r"payments?\b|repayment"))
    periods = list(periods_at(ctx, lsh, (l_pay or l_add or [lease.row])[0]))
    pairs: list[tuple[str, Loc | None, Loc | None, str, Parts]] = []
    for p in periods:
        if rou is not None and l_add:
            rsh = ctx.wb.sheet(rou.sheet)
            r_add = _note_rows(ctx, rou, re.compile(r"addition"))
            if r_add:
                b_ = _val(ctx, lsh, l_add[0], p)
                pairs.append(("ROU additions vs lease-liability additions", _val(ctx, rsh, r_add[0], p), b_, p, [(b_, 1)] if b_ else []))
        if fin is not None and l_int:
            fsh = ctx.wb.sheet(fin.sheet)
            f_int = _note_rows(ctx, fin, re.compile(r"lease"))
            if f_int:
                b_ = _val(ctx, fsh, f_int[0], p)
                pairs.append(("interest on lease liabilities vs finance costs", _val(ctx, lsh, l_int[0], p), b_, p, [(b_, 1)] if b_ else []))
        cf = ctx.statements.sheet("cash_flow")
        if cf and l_pay:
            csh = ctx.wb.sheet(cf)
            rows = [r for r in caption_rows(ctx, csh, re.compile(r"lease")) if row_numbers(ctx, csh, r)]
            vals = [v for v in (_val(ctx, csh, r, p) for r in rows) if v is not None and v.value is not None]
            if vals:
                tot = Loc(csh.name, vals[0].row, vals[0].col, sum((abs(v.value) for v in vals if v.value is not None), ZERO))
                pairs.append(("lease payments vs cash flow", _val(ctx, lsh, l_pay[0], p), tot, p, [(v, 1) for v in vals]))
    for label, a, b, p, parts in pairs:
        if a is None or b is None or a.value is None or b.value is None or abs(a.value) == abs(b.value):
            continue
        if parts and sums_rounding_only(ctx, [(a, 1)], parts, absolute=True):
            continue
        _emit(ctx, "SUB-04", a, abs(b.value), what=f"Leases: {label} ({p}) — {amt(a.value)} vs {amt(b.value)}.",
              evidence=[ev(ctx, a), Evidence(label="Counterpart", ref=b.ref, value=b.value, printed=amt(b.value))],
              fix="Reconcile the ROU / lease-liability roll-forwards with finance costs and the cash flow.", key=f"{label}:{p}", period=p,
              certainty=Certainty.LIKELY)


# =============================================================================== SUB-05 revenue disaggregation

_DISAGG_ROW = re.compile(r"^(india|outside\s+india|domestic|export|overseas|rest\s+of\s+the\s+world|at\s+a\s+point\s+in\s+time|over\s+time|united|europe|asia|americas?)\b")


@check("SUB-05")
def check_revenue_basis(ctx: CheckContext) -> None:
    note = find_note(ctx, re.compile(r"revenue\s+from\s+operations"))
    if note is None:
        return
    sh = ctx.wb.sheet(note.sheet)
    revenue = {p: line_loc(ctx, "revenue", p) for p in ("CY", "PY", "Q4_CY", "Q4_PY")}
    by_caption: dict[tuple[str, str], list[Loc]] = {}
    for r in range(note.row, (note.end_row or sh.max_row) + 1):
        cap = norm_caption(ctx.caption(sh, r))
        if not cap.startswith("total") or sh.row_hidden(r):
            continue
        for p, col in periods_at(ctx, sh, r).items():
            if has_number(ctx, sh, r, col):
                by_caption.setdefault((cap, p), []).append(loc(ctx, sh, r, col))
    for (cap, p), locs in by_caption.items():
        vals = {x.value for x in locs if x.value is not None}
        if len(locs) >= 2 and len(vals) >= 2:
            first, other = locs[0], next(x for x in locs[1:] if x.value != locs[0].value)
            if sums_rounding_only(ctx, [(other, 1)], [(first, 1)]):
                continue
            _emit(ctx, "SUB-05", other, first.value,
                  what=f"Two different totals ({amt(first.value)} and {amt(other.value)}) are captioned '{cap}' in the revenue note ({p}); disaggregations must be on the same basis.",
                  evidence=[ev(ctx, x) for x in locs], fix="Present every disaggregation on the same (net) basis, or caption the gross / partial totals differently.",
                  key=f"basis:{cap}:{p}", period=p)
    # disaggregation tables (geography / timing) must total to revenue
    for t in ctx.tables(sh):
        if not (note.row <= t.r1 <= (note.end_row or sh.max_row)):
            continue
        rows = [r for r in t.rows if _DISAGG_ROW.search(norm_caption(ctx.caption(sh, r)))]
        if len(rows) < 2:
            continue
        tot_row = next((r for r in t.rows if r > rows[-1] and is_total_caption(ctx.caption(sh, r))), None)
        if tot_row is None:
            continue
        for p in periods_at(ctx, sh, tot_row):
            x = _val(ctx, sh, tot_row, p)
            rv = revenue.get(p)
            if x is None or rv is None or x.value is None or rv.value is None or x.value == rv.value:
                continue
            if sums_rounding_only(ctx, [(x, 1)], [(rv, 1)]):
                continue
            _emit(ctx, "SUB-05", x, rv.value,
                  what=f"Revenue disaggregation total ({p}) {amt(x.value)} differs from revenue from operations {amt(rv.value)}.",
                  evidence=[ev(ctx, x, "Disaggregation total"), ev(ctx, rv, "Revenue")], fix="Disaggregate revenue from operations on the same basis as the P&L.",
                  key=f"disagg:{tot_row}:{p}", period=p)


# =============================================================================== SUB-08 financial instruments

_NONFIN = re.compile(
    r"^(other\s+(non[\s-]*)?current\s+(assets|liabilities)|provisions?|deferred\s+tax|current\s+tax|income\s+tax|inventor|total|equity"
    r"|non[\s-]*current\s+(assets|liabilities)|current\s+(assets|liabilities)|liabilities|assets)\b"
)
_STRIP_QUAL = re.compile(r"\s*\(.*?\)|\s*-\s*(non[\s-]*)?current$|^(non[\s-]*)?current\s+")
_COST_INVESTMENTS = re.compile(r"^investments?$|investment\s+in\s+(subsidiar|associate|joint)")


def _item_name(cap: str) -> str:
    c = _STRIP_QUAL.sub("", cap).strip()
    if "outstanding dues" in c:
        return "trade payables"
    return c


def _bs_financial(ctx: CheckContext) -> dict[str, dict[str, list[tuple[int, str]]]]:
    """{'assets'|'liabilities': {item: [(row, caption), …]}} for the Balance Sheet's financial lines."""
    sname = ctx.statements.sheet("balance_sheet")
    out: dict[str, dict[str, list[tuple[int, str]]]] = {"assets": {}, "liabilities": {}}
    if not sname:
        return {}
    sh = ctx.wb.sheet(sname)
    mode: str | None = None
    for r in range(1, sh.max_row + 1):
        cap = norm_caption(ctx.caption(sh, r))
        if not cap:
            continue
        if re.fullmatch(r"financial\s+assets", cap):
            mode = "assets"
            continue
        if re.fullmatch(r"financial\s+liabilities", cap):
            mode = "liabilities"
            continue
        if mode is None:
            continue
        if _NONFIN.search(cap) and "outstanding dues" not in cap:
            mode = None
            continue
        if not row_numbers(ctx, sh, r) or sh.row_hidden(r):
            continue
        out[mode].setdefault(_item_name(cap), []).append((r, cap))
    return out


@check("SUB-08")
def check_financial_instruments(ctx: CheckContext) -> None:
    note = find_note(ctx, re.compile(r"financial\s+instruments|categor(y|ies)\s+of\s+financial|fair\s+value\s+measurement"))
    bs_name = ctx.statements.sheet("balance_sheet")
    if note is None or not bs_name:
        return
    bs = ctx.wb.sheet(bs_name)
    nsh = ctx.wb.sheet(note.sheet)
    fin = _bs_financial(ctx)
    if not fin or not any(fin.values()):
        return
    # FI note rows grouped by section
    fi_rows: dict[str, dict[str, int]] = {"assets": {}, "liabilities": {}}
    fi_total: dict[str, int] = {}
    mode: str | None = None
    for r in range(note.row, (note.end_row or nsh.max_row) + 1):
        cap = norm_caption(ctx.caption(nsh, r))
        if not cap:
            continue
        if re.search(r"^financial\s+assets", cap) and not row_numbers(ctx, nsh, r):
            mode = "assets"
            continue
        if re.search(r"^financial\s+liabilities", cap) and not row_numbers(ctx, nsh, r):
            mode = "liabilities"
            continue
        if mode is None or not row_numbers(ctx, nsh, r):
            continue
        if cap.startswith("total"):
            fi_total[mode] = r
            continue
        fi_rows[mode].setdefault(_item_name(cap), r)
    if not any(fi_rows.values()):
        return
    bcols = ctx.statements.period_cols.get(bs.name, {})
    for side, items in fin.items():
        if not fi_rows[side]:
            continue
        for item, rows in items.items():
            match = max(fi_rows[side], key=lambda k: fuzz.ratio(k, item))
            for p, bcol in bcols.items():
                bvals = [loc(ctx, bs, r, bcol) for r, _cap in rows]
                bvals = [v for v in bvals if v.value is not None]
                if not bvals:
                    continue
                total_bs = sum((v.value for v in bvals if v.value is not None), ZERO)
                if total_bs == 0:
                    continue
                if fuzz.ratio(match, item) < 88:
                    if _COST_INVESTMENTS.search(item):
                        continue  # investments in subsidiaries / associates at cost (Ind AS 27) are outside Ind AS 109 categories
                    kind = "asset" if side == "assets" else "liability"
                    _emit(ctx, "SUB-08", bvals[0], None,
                          what=f"Financial {kind} '{item}' ({p}, {amt(total_bs)}) on the Balance Sheet is missing from the financial-instruments note.",
                          evidence=[ev(ctx, v, "Balance Sheet") for v in bvals], fix="Include every financial asset and liability (current and non-current) in the categories table.",
                          key=f"missing:{side}:{item}:{p}", period=p)
                    continue
                fv = _fi_value(ctx, nsh, fi_rows[side][match], p)
                if fv is None or fv.value is None or fv.value == total_bs or sums_rounding_only(ctx, [(fv, 1)], [(v, 1) for v in bvals]):
                    continue  # separately rounded current / non-current parts differ by print rounding only
                _emit(ctx, "SUB-08", fv, total_bs,
                      what=f"Financial-instruments note shows '{match}' ({p}) of {amt(fv.value)}; the Balance Sheet carries {amt(total_bs)} (current + non-current).",
                      evidence=[ev(ctx, fv, "FI note")] + [ev(ctx, v, "Balance Sheet") for v in bvals],
                      fix="Show the full carrying amount (current and non-current) on the same basis for both years.", key=f"value:{side}:{match}:{p}", period=p)


def _fi_value(ctx: CheckContext, sh: Sheet, row: int, period: str) -> Loc | None:
    pcols = periods_at(ctx, sh, row)
    tcols = [c for c in row_numbers(ctx, sh, row) if re.search(r"\btotal\b", ctx.column_header(sh, c, row).lower())]
    if period in pcols and has_number(ctx, sh, row, pcols[period]):
        return loc(ctx, sh, row, pcols[period])
    if len(tcols) == 1 and not pcols:
        return loc(ctx, sh, row, tcols[0])
    return None
