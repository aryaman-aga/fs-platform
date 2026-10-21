"""XREF-03 mandatory tie-outs (02 §6.3).

Face statements ↔ notes, P&L arithmetic (PBT, tax, PAT, TCI, EPS), SOCE ↔ P&L / BS,
cash flow ↔ P&L / BS / cash note, and note ↔ note links (deferred tax, gratuity
split, depreciation). Comparisons are on displayed values.

Not here (avoid double reporting): the Balance-Sheet tally (BAL-01) and the cash
flow's closing cash vs Balance-Sheet cash (BAL-02) are the casting family's; and a
relationship whose cell is a linear formula of exactly its components is left to
CAST-01 (a printed difference there is a rounding difference of that formula).
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

from rapidfuzz import fuzz

from fscore.checks.base import CheckContext, is_total_caption
from fscore.checks.rollforward import find_segments, table_cols
from fscore.checks.xref import (
    Loc,
    add_pair,
    amt,
    caption_rows,
    cell_period,
    ev,
    find_note,
    has_number,
    is_linear_of,
    line_loc,
    link_target,
    loc,
    mismatch_status,
    periods_at,
    reserve_pairs,
    rounding_only,
    row_numbers,
    same_figure,
    section_of,
)
from fscore.engine.periods import PeriodLabel, parse_period, scope_key
from fscore.engine.statements import NoteLoc, norm_caption
from fscore.io.xlsx_reader import Sheet
from fscore.models import Certainty, Evidence
from fscore.registry import check

ZERO = Decimal(0)
CENT = Decimal("0.01")


def _emit(
    ctx: CheckContext,
    x: Loc,
    expected: Decimal,
    *,
    what: str,
    evidence: list[Evidence],
    fix: str,
    key: str,
    period: str | None = None,
    certainty: Certainty | None = None,
    counterpart: Loc | None = None,
    parts: list[tuple[Loc, int]] | None = None,
    absolute: bool = False,
) -> None:
    check_parts = parts if parts is not None else ([(counterpart, 1)] if counterpart is not None else None)
    if check_parts and rounding_only(ctx, x, check_parts, absolute=absolute):
        return  # same figure through a different rounding path, or an approved balancing adjustment
    sh = ctx.wb.sheet(x.sheet)
    ctx.emit(
        "XREF-03",
        sheet=x.sheet,
        cells=[x.addr],
        caption=ctx.caption(sh, x.row),
        printed=ctx.printed_text(sh, x.row, x.col),
        expected=amt(expected),
        difference=(x.value or ZERO) - expected,
        explanation=what,
        evidence=evidence,
        suggested_fix=fix,
        period=period,
        key=key,
        certainty=certainty,
    )
    if counterpart is not None and counterpart.value is not None and x.value is not None:
        add_pair(ctx, x, counterpart, status=mismatch_status(x.value, counterpart.value), source="mandatory", confirmed=True)


def _pl_periods(ctx: CheckContext) -> list[str]:
    sname = ctx.statements.sheet("profit_loss")
    return list(ctx.statements.period_cols.get(sname or "", {}))


@check("XREF-03")
def check_tie_outs(ctx: CheckContext) -> None:
    _face_vs_notes(ctx)
    _pl_arithmetic(ctx)
    _eps(ctx)
    _soce(ctx)
    _cash_flow(ctx)
    _notes_cross(ctx)


# =============================================================================== face ↔ notes


def _note_cells(ctx: CheckContext, note: NoteLoc, period: str) -> list[tuple[Loc, str, bool]]:
    """Printed numbers of a note block that belong to ``period``: (loc, caption, is_total)."""
    sh = ctx.wb.sheet(note.sheet)
    out: list[tuple[Loc, str, bool]] = []
    tables = [t for t in ctx.tables(sh) if note.row <= t.r2 and t.r1 <= (note.end_row or sh.max_row)]
    for row in range(note.row, (note.end_row or sh.max_row) + 1):
        if sh.row_hidden(row):
            continue
        cols = row_numbers(ctx, sh, row)
        if not cols:
            continue
        cap = norm_caption(ctx.caption(sh, row))
        total = is_total_caption(cap) or any(row in t.total_rows for t in tables)
        for col in cols:
            if cell_period(ctx, sh, row, col) != period:
                continue
            out.append((loc(ctx, sh, row, col), cap, total))
    return out


def _face_vs_notes(ctx: CheckContext) -> None:
    smap = ctx.statements
    for num, refs in smap.note_refs.items():
        note = smap.notes.get(num)
        if note is None:
            continue
        nsh = ctx.wb.sheet(note.sheet)
        for sname, row in refs:
            sh = ctx.wb.sheet(sname)
            face_cap = norm_caption(ctx.caption(sh, row))
            face_sec = section_of(ctx, sh, row)
            for period, col in smap.period_cols.get(sname, {}).items():
                if not has_number(ctx, sh, row, col) or not ctx.is_printed(sh, row, col):
                    continue
                face = loc(ctx, sh, row, col)
                if face.value is None or face.value == 0:
                    continue
                tgt = link_target(ctx, sh, row, col)
                if tgt is not None and tgt[0] == nsh.name and note.row <= tgt[1] <= (note.end_row or nsh.max_row):
                    add_pair(ctx, face, loc(ctx, nsh, tgt[1], tgt[2]), status="ok", source="note_ref", confirmed=True)
                    continue  # direct link into the note: XREF-01 covers format differences
                cells = _note_cells(ctx, note, period)
                if not cells:
                    continue
                equal = [c for c in cells if c[0].value is not None and abs(c[0].value) == abs(face.value)]
                if equal:
                    add_pair(ctx, face, equal[0][0], status="ok", source="note_ref", confirmed=True)
                    continue
                cands = [c for c in cells if c[2]] or (cells if len(cells) == 1 else [])
                if not cands:
                    continue
                if face_sec:
                    same = [c for c in cands if section_of(ctx, nsh, c[0].row, note.row) == face_sec]
                    cands = same or cands
                best = max(cands, key=lambda c: (fuzz.token_set_ratio(c[1], face_cap), -c[0].row if face_sec == "non_current" else c[0].row))
                other = best[0]
                if other.value is None:
                    continue
                _emit(
                    ctx, face, other.value,
                    what=(
                        f"{sname} line '{ctx.caption(sh, row)}' ({period}) prints {amt(face.value)} but Note {num} "
                        f"({note.title}) totals {amt(other.value)} at {other.sheet}!{other.addr}."
                    ),
                    evidence=[ev(ctx, face, f"{sname} line"), ev(ctx, other, f"Note {num}")],
                    fix=f"Link the {sname} line to the Note {num} total (or correct the note).",
                    key=f"note{num}:{period}",
                    period=period,
                    counterpart=other,
                    absolute=True,
                )


# =============================================================================== P&L arithmetic


def _rows_between(ctx: CheckContext, sh: Sheet, r1: int, r2: int, col: int) -> list[Loc]:
    out = []
    for r in range(r1 + 1, r2):
        if sh.row_hidden(r) or not has_number(ctx, sh, r, col) or not ctx.is_printed(sh, r, col):
            continue
        out.append(loc(ctx, sh, r, col))
    return out


def _pl_arithmetic(ctx: CheckContext) -> None:
    sname = ctx.statements.sheet("profit_loss")
    if not sname:
        return
    sh = ctx.wb.sheet(sname)
    for p in _pl_periods(ctx):
        pbt = line_loc(ctx, "pbt", p)
        ti = line_loc(ctx, "total_income", p)
        te = line_loc(ctx, "total_expenses", p)
        ti_parts = [ti] if ti is not None else []
        if ti is None:
            rev, oi = line_loc(ctx, "revenue", p), line_loc(ctx, "other_income", p)
            if rev is not None and oi is not None and rev.value is not None:
                ti = Loc(sh.name, rev.row, rev.col, rev.value + (oi.value or ZERO))
                ti_parts = [rev, oi]
        # PBT = total income − total expenses (± items between expenses and PBT)
        if pbt is not None and ti is not None and te is not None and None not in (pbt.value, ti.value, te.value) and pbt.row > te.row:
            base = ti.value - te.value  # type: ignore[operator]
            between = _rows_between(ctx, sh, te.row, pbt.row, pbt.col)
            s = sum((b.value or ZERO for b in between), ZERO)
            if pbt.value not in (base, base + s, base - s) and not is_linear_of(ctx, pbt, [*ti_parts, te, *between]):
                _emit(ctx, pbt, base, what=f"Profit before tax ({p}) prints {amt(pbt.value)}; total income {amt(ti.value)} less total expenses {amt(te.value)} is {amt(base)}.",
                      evidence=[ev(ctx, ti, "Total income"), ev(ctx, te, "Total expenses"), ev(ctx, pbt, "PBT")],
                      fix="Compute PBT from total income and total expenses.", key=f"pbt:{p}", period=p,
                      parts=[*((x, 1) for x in ti_parts if x is not None), (te, -1)])
        # total tax = current + deferred (+ earlier-year rows between)
        tt, ct = line_loc(ctx, "total_tax", p), line_loc(ctx, "current_tax", p)
        dts = [x for x in ctx.statements.alternates.get("deferred_tax", []) if x.sheet == sname and pbt is not None and x.row > pbt.row]
        dt = None
        if dts and p in dts[0].cols:
            dt = loc(ctx, sh, dts[0].row, dts[0].cols[p])
        if tt is not None and ct is not None and tt.value is not None and ct.value is not None and tt.row > ct.row:
            comps = _rows_between(ctx, sh, ct.row - 1, tt.row, tt.col)
            expected = sum((c.value or ZERO for c in comps), ZERO)
            if tt.value != expected and not is_linear_of(ctx, tt, comps):
                _emit(ctx, tt, expected, what=f"Total tax expense ({p}) prints {amt(tt.value)} but current + deferred tax lines add to {amt(expected)}.",
                      evidence=[ev(ctx, c) for c in comps] + [ev(ctx, tt, "Total tax")],
                      fix="Total tax must be the sum of current and deferred tax.", key=f"tax:{p}", period=p, parts=[(c, 1) for c in comps])
        # PAT = PBT − tax (and must not point at TCI)
        pat, tci = line_loc(ctx, "pat", p), line_loc(ctx, "tci", p)
        tax_v: Decimal | None = None
        if tt is not None and tt.value is not None:
            tax_v = tt.value
        elif ct is not None and ct.value is not None:
            tax_v = ct.value + (dt.value if dt is not None and dt.value is not None else ZERO)
        if pat is not None and pbt is not None and pat.value is not None and pbt.value is not None and tax_v is not None:
            expected = pbt.value - tax_v
            if pat.value != expected:
                parts = [pbt] + ([tt] if tt is not None else [x for x in (ct, dt) if x is not None])
                points_at_tci = tci is not None and tci.value is not None and (
                    pat.value == tci.value or (link_target(ctx, sh, pat.row, pat.col) or (None, 0, 0))[:3] == (tci.sheet, tci.row, tci.col))
                if points_at_tci or not is_linear_of(ctx, pat, parts):
                    what = (f"Profit after tax ({p}) prints {amt(pat.value)}, which equals total comprehensive income; PBT {amt(pbt.value)} less tax {amt(tax_v)} is {amt(expected)}."
                            if points_at_tci else f"Profit after tax ({p}) prints {amt(pat.value)}; PBT {amt(pbt.value)} less tax {amt(tax_v)} is {amt(expected)}.")
                    _emit(ctx, pat, expected, what=what, evidence=[ev(ctx, pbt, "PBT"), ev(ctx, pat, "PAT")] + ([ev(ctx, tci, "TCI")] if tci else []),
                          fix="Profit for the period = profit before tax − total tax expense.", key=f"pat:{p}", period=p,
                          parts=None if points_at_tci else [(pbt, 1), *((x, -1) for x in parts[1:] if x is not None)])
        # TCI = PAT + OCI
        oci = line_loc(ctx, "oci_total", p)
        if tci is not None and pat is not None and tci.value is not None and pat.value is not None and tci.row > pat.row:
            if oci is not None and oci.value is not None and pat.row < oci.row < tci.row:
                parts = [pat, oci]
                expected = pat.value + oci.value
            else:
                items = [x for x in _rows_between(ctx, sh, pat.row, tci.row, tci.col) if not norm_caption(ctx.caption(sh, x.row)).startswith("total")]
                parts = [pat, *items]
                expected = pat.value + sum((i.value or ZERO for i in items), ZERO)
            if tci.value != expected and not is_linear_of(ctx, tci, parts):
                _emit(ctx, tci, expected, what=f"Total comprehensive income ({p}) prints {amt(tci.value)}; profit {amt(pat.value)} plus OCI gives {amt(expected)}.",
                      evidence=[ev(ctx, x) for x in parts] + [ev(ctx, tci, "TCI")], fix="TCI = profit for the period + other comprehensive income.", key=f"tci:{p}", period=p,
                      parts=[(x, 1) for x in parts])


# =============================================================================== EPS

_EPS_NOTE = re.compile(r"earnings?\s+per\s+(equity\s+)?share")
_NUMERATOR = re.compile(r"(profit|loss).*(attributable|for the (year|period)|after tax)|net profit|^profit|^\(?loss\)?")
_SHARES = re.compile(r"weighted\s+average\s+(number|no\.?)\s+of\s+(equity\s+)?shares")
_BASIC = re.compile(r"^basic|basic\s+(and|&)\s+diluted|basic\s+earnings|earnings\s+per\s+share.*basic")


def _eps(ctx: CheckContext) -> None:
    mult = Decimal(ctx.config.unit_divisor)
    note = find_note(ctx, _EPS_NOTE)
    pl_eps: dict[str, Loc] = {}
    for key in ("eps_basic", "eps"):
        for ln in ctx.statements.alternates.get(key, []):
            if ln.sheet != ctx.statements.sheet("profit_loss"):
                continue
            sh = ctx.wb.sheet(ln.sheet)
            for p, col in ln.cols.items():
                if p not in pl_eps and has_number(ctx, sh, ln.row, col):
                    pl_eps[p] = loc(ctx, sh, ln.row, col)
        if pl_eps:
            break
    note_vals: dict[str, dict[str, Loc]] = {}
    if note is not None:
        nsh = ctx.wb.sheet(note.sheet)
        for row in range(note.row, (note.end_row or nsh.max_row) + 1):
            cap = norm_caption(ctx.caption(nsh, row))
            kind = "shares" if _SHARES.search(cap) else "eps" if _BASIC.search(cap) or (_EPS_NOTE.search(cap) and "face" not in cap) else (
                "num" if _NUMERATOR.search(cap) and "per share" not in cap else None)
            if kind is None:
                continue
            for p, col in periods_at(ctx, nsh, row).items():
                if has_number(ctx, nsh, row, col):
                    note_vals.setdefault(p, {}).setdefault(kind, loc(ctx, nsh, row, col))
    for p, vals in note_vals.items():
        num, shares, eps = vals.get("num"), vals.get("shares"), vals.get("eps")
        pat = line_loc(ctx, "pat", p)
        if num is not None and pat is not None and num.value is not None and pat.value is not None and abs(num.value) != abs(pat.value):
            _emit(ctx, num, pat.value, what=f"EPS numerator ({p}) {amt(num.value)} differs from profit for the period {amt(pat.value)}.",
                  evidence=[ev(ctx, num, "EPS note numerator"), ev(ctx, pat, "P&L profit")], fix="Use profit for the period attributable to equity shareholders.",
                  key=f"eps_num:{p}", period=p, counterpart=pat)
        if num is not None and shares is not None and eps is not None and None not in (num.value, shares.value, eps.value) and shares.value:
            expected = (num.value * mult / shares.value).quantize(CENT, rounding=ROUND_HALF_UP)  # type: ignore[operator]
            if abs(expected - eps.value) > CENT:  # type: ignore[operator]
                _emit(ctx, eps, expected, what=f"EPS ({p}) prints {amt(eps.value)}; {amt(num.value)} × {int(mult):,} ÷ {int(shares.value or 0):,} weighted shares = {amt(expected)}.",
                      evidence=[ev(ctx, num, "Numerator"), ev(ctx, shares, "Weighted shares"), ev(ctx, eps, "EPS")],
                      fix="Recompute EPS = profit × unit ÷ weighted average shares of the same period.", key=f"eps_calc:{p}", period=p)
        if eps is not None and p in pl_eps and eps.value is not None and pl_eps[p].value is not None and eps.value != pl_eps[p].value:
            _emit(ctx, pl_eps[p], eps.value, what=f"EPS on the face of the P&L ({p}) {amt(pl_eps[p].value)} differs from the EPS note {amt(eps.value)}.",
                  evidence=[ev(ctx, pl_eps[p], "P&L EPS"), ev(ctx, eps, "EPS note")], fix="Link the P&L EPS to the EPS note.", key=f"eps_face:{p}", period=p, counterpart=eps)
    # identical EPS for both years while profits differ (both point at one period)
    for cy, py in (("CY", "PY"), ("Q4_CY", "Q4_PY")):
        a, b = pl_eps.get(cy), pl_eps.get(py)
        pa, pb = line_loc(ctx, "pat", cy), line_loc(ctx, "pat", py)
        if a is None or b is None or pa is None or pb is None:
            continue
        if a.value is not None and a.value == b.value and a.value != 0 and pa.value != pb.value:
            _emit(ctx, a, ZERO, what=f"EPS is {amt(a.value)} for both periods although profit differs ({amt(pa.value)} vs {amt(pb.value)}): one period's EPS points at the other.",
                  evidence=[ev(ctx, a, cy), ev(ctx, b, py), ev(ctx, pa, f"PAT {cy}"), ev(ctx, pb, f"PAT {py}")],
                  fix="Point each EPS at its own period's profit and weighted shares.", key=f"eps_same:{cy}", period=cy)


# =============================================================================== SOCE


def _soce(ctx: CheckContext) -> None:
    sname = ctx.statements.sheet("soce")
    if not sname:
        return
    sh = ctx.wb.sheet(sname)
    tables = ctx.tables(sh)
    if not tables:
        return
    headers: dict[int, str] = {}
    for t in tables:
        for c, h in t.headers.items():
            headers.setdefault(c, norm_caption(h))
    total_col = next((c for c, h in sorted(headers.items(), reverse=True) if re.search(r"\btotal\b", h)), None)
    sc_col = next((c for c, h in headers.items() if "share capital" in h), None)
    reserve_cols = [c for c, h in headers.items() if c not in (total_col, sc_col) and h]
    # dated balance rows -> period
    balances: list[tuple[int, str]] = []
    for row in range(1, sh.max_row + 1):
        cap = norm_caption(ctx.caption(sh, row))
        if not re.search(r"balance|as at", cap) or not row_numbers(ctx, sh, row):
            continue
        lab = parse_period(cap)
        if lab is None or lab.end is None:
            continue
        k = scope_key(PeriodLabel(lab.end, None, "point", ""), ctx.config)
        if k:
            balances.append((row, k))
    # closing balances vs the Balance Sheet and vs the other-equity note
    seen: set[str] = set()
    for row, p in reversed(balances):
        if p in seen:
            continue
        seen.add(p)
        targets = []
        if total_col is not None:
            targets.append((total_col, "total_equity" if sc_col is not None else "other_equity"))
        if sc_col is not None:
            targets.append((sc_col, "share_capital"))
        for col, key in targets:
            x = loc(ctx, sh, row, col)
            bs = line_loc(ctx, key, p)
            if x.value is None or bs is None or bs.value is None or x.value == bs.value:
                continue
            _emit(ctx, x, bs.value, what=f"SOCE closing balance ({p}) prints {amt(x.value)} but the Balance Sheet shows {amt(bs.value)} for {key.replace('_', ' ')}.",
                  evidence=[ev(ctx, x, "SOCE"), ev(ctx, bs, "Balance Sheet")], fix="The SOCE closing balance must equal the Balance Sheet.",
                  key=f"soce_close:{key}:{p}", period=p, counterpart=bs)
        if reserve_cols and (total_col is None or sc_col is None):
            oe = line_loc(ctx, "other_equity", p)
            vals = [ctx.displayed(sh, row, c) for c in reserve_cols]
            if oe is not None and oe.value is not None and all(v is not None for v in vals):
                s_ = sum((v for v in vals if v is not None), ZERO)
                if s_ != oe.value:
                    x = loc(ctx, sh, row, reserve_cols[-1])
                    _emit(ctx, x, oe.value, what=f"SOCE reserves at the period end ({p}) add to {amt(s_)} but other equity on the Balance Sheet is {amt(oe.value)}.",
                          evidence=[ctx.evidence_for(sh, row, c) for c in reserve_cols] + [ev(ctx, oe, "Other equity")],
                          fix="Reconcile the SOCE reserve columns with other equity.", key=f"soce_oe:{p}", period=p)
    for a, b, label in reserve_pairs(ctx):
        if a.value == b.value:
            add_pair(ctx, a, b, status="ok", source="mandatory", confirmed=True)
            continue
        _emit(ctx, a, b.value or ZERO, what=f"SOCE '{label}' closing balance prints {amt(a.value)} but the other-equity note shows {amt(b.value)} for the same reserve.",
              evidence=[ev(ctx, a, "SOCE"), ev(ctx, b, "Other-equity note")], fix="Each SOCE reserve column must equal the reserve in the other-equity note.",
              key=f"soce_reserve:{label}", counterpart=b)
    # profit and OCI rows vs the P&L (period = the balance row that closes the block)
    for key, pl_key in (("soce_profit", "pat"), ("soce_oci", "oci_total")):
        for ln in ctx.statements.alternates.get(key, []):
            if ln.sheet != sname:
                continue
            if key == "soce_oci" and "total" in norm_caption(ln.caption):
                continue
            nxt = [p for r, p in balances if r > ln.row]
            if not nxt:
                continue
            p = nxt[0]
            pl = line_loc(ctx, pl_key, p)
            if pl is None or pl.value is None:
                continue
            comps = [c for c in reserve_cols if has_number(ctx, sh, ln.row, c)]
            tot = loc(ctx, sh, ln.row, total_col) if total_col is not None and has_number(ctx, sh, ln.row, total_col) else None
            bad = [loc(ctx, sh, ln.row, c) for c in comps]
            bad = [x for x in bad if x.value is not None and x.value != ZERO and x.value != pl.value]
            tot_bad = tot is not None and tot.value is not None and tot.value != pl.value
            bad = [x for x in bad if not same_figure(ctx, x, pl)]
            tot_bad = tot_bad and tot is not None and not same_figure(ctx, tot, pl)
            if not bad and not tot_bad:
                continue
            first = bad[0] if bad else tot
            assert first is not None
            cells = [first.addr] + ([tot.addr] if tot_bad and tot is not None and tot.addr != first.addr else [])
            ctx.emit(
                "XREF-03", sheet=sh.name, cells=cells, caption=ln.caption, printed=ctx.printed_text(sh, first.row, first.col),
                expected=amt(pl.value), difference=(first.value or ZERO) - pl.value,
                explanation=f"SOCE '{ln.caption}' ({p}) prints {amt(first.value)} but the Statement of Profit and Loss shows {amt(pl.value)}.",
                evidence=[ev(ctx, first, "SOCE")] + ([ev(ctx, tot, "SOCE total")] if tot_bad and tot is not None else []) + [ev(ctx, pl, "P&L")],
                suggested_fix="Link the SOCE movement to the Statement of Profit and Loss.", key=f"{key}:{p}", period=p,
            )
            add_pair(ctx, first, pl, status=mismatch_status(first.value or ZERO, pl.value), source="mandatory", confirmed=True)


# =============================================================================== cash flow

_WC = re.compile(r"(increase|decrease)\W*(?:/\s*\(?(?:increase|decrease)\)?\s*)?in\s+(.+)$")


def _bs_rows(ctx: CheckContext) -> list[tuple[int, str, str | None, bool]]:
    """Balance Sheet rows: (row, caption, section, is_asset)."""
    sname = ctx.statements.sheet("balance_sheet")
    if not sname:
        return []
    sh = ctx.wb.sheet(sname)
    ta = ctx.statements.line("total_assets")
    eq_row = None
    for r in range(1, sh.max_row + 1):
        if re.search(r"^equity\s+and\s+liabilities|^equity\s*&\s*liabilities", norm_caption(ctx.caption(sh, r))):
            eq_row = r
            break
    split = eq_row or (ta.row + 1 if ta else sh.max_row)
    out = []
    for r in range(1, sh.max_row + 1):
        cap = norm_caption(ctx.caption(sh, r))
        if not cap or not row_numbers(ctx, sh, r):
            continue
        if "outstanding dues" in cap:
            cap = "trade payables"  # Schedule III shows trade payables as MSE / other creditors' dues
        elif cap.startswith("total"):
            continue
        out.append((r, cap, section_of(ctx, sh, r), r < split))
    return out


def _cash_flow(ctx: CheckContext) -> None:
    sname = ctx.statements.sheet("cash_flow")
    if not sname:
        return
    sh = ctx.wb.sheet(sname)
    cf_periods = list(ctx.statements.period_cols.get(sname, {}))
    for p in cf_periods:
        for cf_key, pl_key, label in (("cf_pbt", "pbt", "Profit before tax"), ("cf_dep", "depreciation", "Depreciation and amortisation")):
            a, b = line_loc(ctx, cf_key, p), line_loc(ctx, pl_key, p)
            if a is None or b is None or a.value is None or b.value is None or abs(a.value) == abs(b.value):
                continue
            _emit(ctx, a, b.value, what=f"Cash flow '{label}' ({p}) prints {amt(a.value)} but the Statement of Profit and Loss shows {amt(b.value)}.",
                  evidence=[ev(ctx, a, "Cash flow"), ev(ctx, b, "P&L")], fix=f"Link the cash-flow {label.lower()} to the Statement of Profit and Loss.",
                  key=f"{cf_key}:{p}", period=p, counterpart=b, absolute=True)
        fc_rows = caption_rows(ctx, sh, re.compile(r"^finance\s+costs?$|^interest\s+expense"))
        fin = line_loc(ctx, "finance_costs", p)
        col = ctx.statements.period_cols.get(sname, {}).get(p)
        if fc_rows and fin is not None and fin.value is not None and col is not None and has_number(ctx, sh, fc_rows[0], col):
            x = loc(ctx, sh, fc_rows[0], col)
            if x.value is not None and abs(x.value) != abs(fin.value):
                _emit(ctx, x, fin.value, what=f"Finance costs added back in the cash flow ({p}) {amt(x.value)} differ from the P&L finance costs {amt(fin.value)}.",
                      evidence=[ev(ctx, x, "Cash flow"), ev(ctx, fin, "P&L")], fix="Add back the P&L finance costs.", key=f"cf_fin:{p}", period=p,
                      certainty=Certainty.LIKELY, counterpart=fin, absolute=True)
        # A + B + C = net change
        a, b, c, n = (line_loc(ctx, k, p) for k in ("cf_op_total", "cf_inv_total", "cf_fin_total", "cf_net_change"))
        if (a is not None and b is not None and c is not None and n is not None
                and a.value is not None and b.value is not None and c.value is not None and n.value is not None):
            expected = a.value + b.value + c.value
            if n.value != expected and not is_linear_of(ctx, n, [a, b, c]):
                _emit(ctx, n, expected, what=f"Net change in cash ({p}) prints {amt(n.value)}; A + B + C = {amt(expected)}.",
                      evidence=[ev(ctx, x) for x in (a, b, c, n)], fix="Net change in cash = A + B + C.", key=f"cf_abc:{p}", period=p,
                      parts=[(a, 1), (b, 1), (c, 1)])
        # closing = opening + net change (+ FX effect rows between)
        op, cl = line_loc(ctx, "cf_opening", p), line_loc(ctx, "cf_closing", p)
        if n is not None and op is not None and cl is not None and None not in (n.value, op.value, cl.value) and cl.row > op.row:
            extra = _rows_between(ctx, sh, max(n.row, op.row), cl.row, cl.col)
            expected = n.value + op.value + sum((x.value or ZERO for x in extra), ZERO)  # type: ignore[operator]
            if cl.value != expected and not is_linear_of(ctx, cl, [n, op, *extra]):
                _emit(ctx, cl, expected, what=f"Closing cash ({p}) prints {amt(cl.value)}; opening {amt(op.value)} plus net change {amt(n.value)} is {amt(expected)}.",
                      evidence=[ev(ctx, op), ev(ctx, n), ev(ctx, cl)], fix="Closing cash = opening cash + net change (+ exchange differences).", key=f"cf_close:{p}", period=p,
                      parts=[(n, 1), (op, 1), *((x, 1) for x in extra)])
    _working_capital(ctx, sh)
    _cash_components(ctx, sh)


def _working_capital(ctx: CheckContext, sh: Sheet) -> None:
    bs_name = ctx.statements.sheet("balance_sheet")
    cf_cols = ctx.statements.period_cols.get(sh.name, {})
    if not bs_name or "CY" not in cf_cols:
        return
    bs = ctx.wb.sheet(bs_name)
    bcols = ctx.statements.period_cols.get(bs_name, {})
    if "CY" not in bcols or "PY" not in bcols:
        return
    rows = _bs_rows(ctx)
    col = cf_cols["CY"]
    for r in range(1, sh.max_row + 1):
        cap = norm_caption(ctx.caption(sh, r))
        m = _WC.search(cap)
        if not m or not has_number(ctx, sh, r, col):
            continue
        item = re.sub(r"\(.*?\)|\band\b.*$", "", m.group(2)).strip()
        item = re.sub(r"^(other\s+)?(current\s+|non[\s-]*current\s+)", lambda mm: mm.group(1) or "", item).strip()
        matches = [(br, bcap, sec, asset) for br, bcap, sec, asset in rows if fuzz.ratio(re.sub(r"^(other\s+)?(current\s+|non[\s-]*current\s+)", lambda mm: mm.group(1) or "", bcap), item) >= 90]
        if not matches:
            continue
        x = loc(ctx, sh, r, col)
        if x.value is None:
            continue

        def delta(group: list[tuple[int, str, str | None, bool]]) -> Decimal | None:
            tot = ZERO
            for br, _bcap, _sec, asset in group:
                cy, py = ctx.displayed(bs, br, bcols["CY"]), ctx.displayed(bs, br, bcols["PY"])
                if cy is None and py is None:
                    return None
                d = (cy or ZERO) - (py or ZERO)
                tot += -d if asset else d
            return tot

        options = [matches, [m_ for m_ in matches if m_[2] == "current"], [m_ for m_ in matches if m_[2] == "non_current"]]
        values = [delta(g) for g in options if g]
        if any(v is not None and v == x.value for v in values):
            continue
        refs = {(bs.name, br, c_) for br, *_ in matches for c_ in (bcols["CY"], bcols["PY"])}
        node = ctx.parsed(sh, sh.cell(r, col))  # type: ignore[arg-type]
        if node is not None:
            from fscore.engine import formula as F

            used = set()
            for rf in F.references(node):
                if rf.parts.kind != "cell":
                    continue
                tgt = ctx.wb.get_sheet(rf.sheet) if rf.sheet else sh
                used.add((tgt.name if tgt is not None else str(rf.sheet), rf.parts.r1, rf.parts.c1))
            if used and used <= refs:
                continue  # computed from these very Balance Sheet cells: a print-rounding difference only
        expected = next((v for v in values if v is not None), None)
        if expected is None:
            continue
        group = next((g for g in options if g and delta(g) == expected), matches)
        wc_parts = [(loc(ctx, bs, br, bcols[k]), (-1 if asset else 1) * (1 if k == "CY" else -1)) for br, _bc, _sec, asset in group for k in ("CY", "PY")]
        _emit(ctx, x, expected, parts=wc_parts,
              what=f"Working-capital line '{ctx.caption(sh, r)}' prints {amt(x.value)} but the Balance Sheet movement of {', '.join(sorted({m_[1] for m_ in matches}))} is {amt(expected)}.",
              evidence=[ev(ctx, x, "Cash flow")] + [ctx.evidence_for(bs, br, bcols[k]) for br, *_ in matches for k in ("CY", "PY")],
              fix="Derive the movement from the Balance Sheet (CY − PY, sign-aware), or explain the non-cash / reclassified part.",
              key=f"wc:{r}", period="CY", certainty=Certainty.LIKELY)


def _cash_components(ctx: CheckContext, sh: Sheet) -> None:
    note = find_note(ctx, re.compile(r"cash\s+and\s+cash\s+equivalents"))
    if note is None:
        return
    head = caption_rows(ctx, sh, re.compile(r"components?\s+of\s+cash|cash\s+and\s+cash\s+equivalents\s+(comprise|include|consist)"))
    if not head:
        return
    cf_cols = ctx.statements.period_cols.get(sh.name, {})
    comp: list[tuple[int, str]] = []
    total_row = None
    for r in range(head[0] + 1, min(sh.max_row, head[0] + 15) + 1):
        cap = norm_caption(ctx.caption(sh, r))
        if not cap:
            continue
        if not row_numbers(ctx, sh, r):
            if comp:
                break
            continue
        if cap.startswith("total") or re.match(r"^cash\s+and\s+cash\s+equivalents", cap):
            total_row = r
            break
        comp.append((r, cap))
    if not comp:
        return
    nsh = ctx.wb.sheet(note.sheet)
    for p, col in cf_cols.items():
        note_items: list[tuple[Loc, str]] = []
        for x, cap, is_tot in _note_cells(ctx, note, p):
            if not is_tot and x.value is not None:
                note_items.append((x, cap))
        if not note_items:
            continue
        matched_note: set[int] = set()
        for r, cap in comp:
            x = loc(ctx, sh, r, col)
            if x.value is None:
                continue
            best = max(note_items, key=lambda it: fuzz.token_set_ratio(it[1], cap))
            if fuzz.token_set_ratio(best[1], cap) < 85:
                continue
            matched_note.add(best[0].row)
            if best[0].value != x.value:
                _emit(ctx, x, best[0].value or ZERO,
                      what=f"Cash-flow component '{ctx.caption(sh, r)}' ({p}) prints {amt(x.value)} but the cash note shows {amt(best[0].value)}.",
                      evidence=[ev(ctx, x, "Cash flow component"), ev(ctx, best[0], "Cash note")],
                      fix="Show the components of cash exactly as in the cash and cash equivalents note.", key=f"cfcomp:{r}:{p}", period=p, counterpart=best[0])
        for nx, _ncap in note_items:
            if nx.row in matched_note or not nx.value:
                continue
            anchor_row = total_row or comp[-1][0]
            x = loc(ctx, sh, anchor_row, col)
            _emit(ctx, x, nx.value,
                  what=f"The cash note shows '{ctx.caption(nsh, nx.row)}' of {amt(nx.value)} ({p}) but the cash-flow components omit it.",
                  evidence=[ev(ctx, nx, "Cash note")], fix="Add the missing component (or reclassify) so the components agree with the cash note.",
                  key=f"cfmiss:{nx.row}:{p}", period=p)


# =============================================================================== notes ↔ notes

_DEP_PPE = re.compile(r"property,?\s+plant|tangible")
_DEP_ROU = re.compile(r"right[\s-]+of[\s-]+use")
_DEP_INT = re.compile(r"amorti[sz]ation|intangible")


def _notes_cross(ctx: CheckContext) -> None:
    _deferred_tax(ctx)
    _gratuity_split(ctx)
    _depreciation(ctx)


def _deferred_tax(ctx: CheckContext) -> None:
    pl = ctx.statements.sheet("profit_loss")
    if not pl:
        return
    plsh = ctx.wb.sheet(pl)
    pbt = ctx.statements.line("pbt")
    oci_tax_rows = caption_rows(ctx, plsh, re.compile(r"income\s+tax\s+relating|tax\s+(effect|on)\b.*(oci|other comprehensive|items)"))
    for sh in ctx.print_sheets:
        for t in ctx.tables(sh):
            if not re.search(r"deferred\s+tax", (t.title or "").lower()):
                continue
            hdr = {c: (t.headers.get(c) or "").lower() for c in table_cols(t)}
            pl_col = next((c for c, h in hdr.items() if re.search(r"profit|loss|statement of", h) and "comprehensive" not in h), None)
            oci_col = next((c for c, h in hdr.items() if re.search(r"\boci\b|comprehensive", h)), None)
            dates = [parse_period(h) for h in hdr.values()]
            ends = [d.end for d in dates if d is not None and d.end is not None]
            if not ends or (pl_col is None and oci_col is None):
                continue
            p = scope_key(PeriodLabel(max(ends), None, "point", ""), ctx.config)
            if p is None:
                continue
            total = next((r for r in reversed(t.rows) if is_total_caption(ctx.caption(sh, r))), None)
            if total is None:
                continue
            checks = []
            if pl_col is not None:
                dts = [x for x in ctx.statements.alternates.get("deferred_tax", []) if x.sheet == pl and (pbt is None or x.row > pbt.row)]
                if dts and p in dts[0].cols:
                    checks.append((pl_col, loc(ctx, plsh, dts[0].row, dts[0].cols[p]), "deferred tax in the Statement of Profit and Loss"))
            if oci_col is not None and oci_tax_rows:
                pcols = ctx.statements.period_cols.get(pl, {})
                if p in pcols:
                    checks.append((oci_col, loc(ctx, plsh, oci_tax_rows[0], pcols[p]), "tax on OCI items in the Statement of Profit and Loss"))
            for col, target, label in checks:
                x = loc(ctx, sh, total, col)
                if x.value is None or target.value is None or abs(x.value) == abs(target.value):
                    continue
                _emit(ctx, x, target.value, what=f"Deferred-tax table total '{(t.headers.get(col) or '').strip()}' ({p}) {amt(x.value)} differs from the {label} {amt(target.value)}.",
                      evidence=[ev(ctx, x, "Deferred-tax table"), ev(ctx, target, "P&L")], fix="Reconcile the deferred-tax movement with the P&L / OCI tax lines.",
                      key=f"dt:{col}:{p}", period=p, counterpart=target, absolute=True)


def _gratuity_split(ctx: CheckContext) -> None:
    prov = find_note(ctx, re.compile(r"^provisions?$|^provisions?\b"))
    if prov is None:
        return
    psh = ctx.wb.sheet(prov.sheet)
    prov_rows: dict[str, int] = {}
    for r in range(prov.row, (prov.end_row or psh.max_row) + 1):
        cap = norm_caption(ctx.caption(psh, r))
        if "gratuity" in cap and row_numbers(ctx, psh, r):
            sec = section_of(ctx, psh, r, prov.row)
            if sec:
                prov_rows.setdefault(sec, r)
    if not prov_rows:
        return
    # actuarial split rows: "Current" / "Non-current" (liability) inside a gratuity disclosure
    for sh in ctx.print_sheets:
        for r in range(1, sh.max_row + 1):
            cap = norm_caption(ctx.caption(sh, r))
            if not re.fullmatch(r"(non[\s-]*current|current)(\s+(liability|portion|provision))?", cap):
                continue
            context = " ".join(norm_caption(ctx.caption(sh, rr)) for rr in range(max(1, r - 15), r))
            if not re.search(r"gratuity|defined\s+benefit|obligation", context):
                continue
            if sh.name == prov.sheet and prov.row <= r <= (prov.end_row or psh.max_row) and "gratuity" not in context:
                continue
            sec = "non_current" if cap.startswith("non") else "current"
            if sec not in prov_rows:
                continue
            for p, col in periods_at(ctx, sh, r).items():
                pcol = periods_at(ctx, psh, prov_rows[sec]).get(p)
                if pcol is None or not has_number(ctx, sh, r, col):
                    continue
                x, y = loc(ctx, sh, r, col), loc(ctx, psh, prov_rows[sec], pcol)
                if x.value is None or y.value is None or x.value == y.value or (x.sheet, x.row) == (y.sheet, y.row):
                    continue
                _emit(ctx, x, y.value, what=f"Gratuity {sec.replace('_', '-')} portion per the actuarial disclosure ({p}) {amt(x.value)} differs from the provisions note {amt(y.value)}.",
                      evidence=[ev(ctx, x, "Actuarial split"), ev(ctx, y, "Provisions note")], fix="Use the actuarial current / non-current split in the provisions note.",
                      key=f"grat:{sec}:{p}", period=p, counterpart=y)


def _depreciation(ctx: CheckContext) -> None:
    dep_note = find_note(ctx, re.compile(r"depreciation"))
    if dep_note is None:
        return
    dsh = ctx.wb.sheet(dep_note.sheet)
    dep_lines: dict[str, int] = {}
    for r in range(dep_note.row + 1, (dep_note.end_row or dsh.max_row) + 1):
        cap = norm_caption(ctx.caption(dsh, r))
        if not row_numbers(ctx, dsh, r) or cap.startswith("total"):
            continue
        for k, rx in (("rou", _DEP_ROU), ("ppe", _DEP_PPE), ("int", _DEP_INT)):
            if k not in dep_lines and rx.search(cap):
                dep_lines[k] = r
                break
    if not dep_lines:
        return
    for k, title_rx in (("ppe", re.compile(r"property,?\s+plant")), ("rou", _DEP_ROU), ("int", re.compile(r"intangible"))):
        if k not in dep_lines:
            continue
        note = find_note(ctx, title_rx)
        if note is None:
            continue
        nsh = ctx.wb.sheet(note.sheet)
        for t in ctx.tables(nsh):
            if not (note.row <= t.r1 <= (note.end_row or nsh.max_row)):
                continue
            total_col = next((c for c in reversed(table_cols(t)) if re.search(r"\btotal\b", (t.headers.get(c) or "").lower())), None)
            for seg in find_segments(ctx, nsh, t):
                if seg.layout == "dated" and not re.search(r"depreciation|amorti", seg.section):
                    continue
                for r in seg.moves:
                    cap = norm_caption(ctx.caption(nsh, r, before_col=min(t.num_cols)))
                    if not re.search(r"charge|depreciation|amorti", cap) or re.search(r"disposal|deletion|accumulated", cap):
                        continue
                    if seg.layout == "dated" and seg.end is not None:
                        p = scope_key(PeriodLabel(seg.end, None, "point", ""), ctx.config)
                        cols = [total_col] if total_col is not None else (table_cols(t)[-1:] if len(table_cols(t)) == 1 else [])
                        pairs = [(p, c) for c in cols if p]
                    else:
                        pairs = []
                        for c in table_cols(t):
                            pk = cell_period(ctx, nsh, r, c)
                            if pk:
                                pairs.append((pk, c))
                    for p, c in pairs:
                        dcol = periods_at(ctx, dsh, dep_lines[k]).get(p)
                        if dcol is None or not has_number(ctx, nsh, r, c):
                            continue
                        x, y = loc(ctx, nsh, r, c), loc(ctx, dsh, dep_lines[k], dcol)
                        if x.value is None or y.value is None or abs(x.value) == abs(y.value):
                            continue
                        _emit(ctx, y, abs(x.value),
                              what=f"Depreciation note line '{ctx.caption(dsh, dep_lines[k])}' ({p}) {amt(y.value)} differs from the charge in the {note.title} schedule {amt(x.value)}.",
                              evidence=[ev(ctx, x, "Schedule charge"), ev(ctx, y, "Depreciation note")],
                              fix="Reconcile the depreciation charge between the asset schedule, the depreciation note and the P&L.",
                              key=f"dep:{k}:{p}", period=p, counterpart=x, absolute=True)
