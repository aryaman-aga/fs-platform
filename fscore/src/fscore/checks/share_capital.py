"""Share capital checks (02 §6.4): SUB-06 arithmetic, SUB-07 shareholding percentages.

* SUB-06: shares × face value = amount for every class and date (captions such as
  "N equity shares of ₹ 10 each" and the reconciliation table), and share-count
  totals that double-count an intermediate subtotal inside their own range.
* SUB-07: % holding = shares ÷ total shares; the same holder shows the same % in
  the > 5 % holders and promoter tables; promoter "% change" = CY % − PY %.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from rapidfuzz import fuzz

from fscore.checks.base import CheckContext, is_total_caption
from fscore.checks.xref import (
    Loc,
    amt,
    cell_period,
    ev,
    has_number,
    loc,
    periods_at,
    row_numbers,
    same_figure,
)
from fscore.engine.display import format_info
from fscore.engine.statements import NoteLoc, norm_caption
from fscore.engine.tables import Table
from fscore.io.xlsx_reader import Sheet
from fscore.registry import check

ZERO = Decimal(0)
_NUM = r"(\d{1,3}(?:,\d{2,3})+|\d+)"
_COUNT_FV = re.compile(
    rf"{_NUM}\s*(?:\((?:[^)]*?)\))?\s*(?:fully\s+paid[\s-]*up\s+)?(?:equity|preference|ordinary)?\s*shares?\s+of\s+(?:₹|rs\.?|inr|re\.?)\s*([\d.]+)\s*(?:/-)?\s*each",
    re.I,
)
_PY_COUNT = re.compile(rf"\(\s*(?:as\s+(?:at|on)\s+)?(?:[a-z]+\s+\d{{1,2}},?\s+\d{{4}}|\d{{1,2}}\s+[a-z]+,?\s+\d{{4}}|previous\s+year|py)\s*[:\-–]\s*{_NUM}\s*\)", re.I)
_FV_TEXT = re.compile(r"(?:face|nominal|par)\s+value\s+(?:of\s+)?(?:₹|rs\.?|inr|re\.?)\s*([\d.]+)|(?:₹|rs\.?|inr|re\.?)\s*([\d.]+)\s*(?:/-)?\s*each", re.I)


def _int(text: str) -> int | None:
    try:
        return int(text.replace(",", ""))
    except ValueError:
        return None


def _dec(text: str) -> Decimal | None:
    try:
        return Decimal(text.rstrip("."))
    except InvalidOperation:
        return None


def _share_notes(ctx: CheckContext) -> list[NoteLoc]:
    out = []
    for n in ctx.statements.notes.values():
        if re.search(r"share\s+capital", n.title.lower()):
            out.append(n)
    return out


def _face_value(ctx: CheckContext, sh: Sheet, note: NoteLoc) -> Decimal | None:
    for r in range(note.row, (note.end_row or sh.max_row) + 1):
        for c in range(1, sh.max_col + 1):
            cell = sh.cell(r, c)
            if cell is None or not isinstance(cell.value, str):
                continue
            m = _FV_TEXT.search(cell.value)
            if m:
                return _dec(m.group(1) or m.group(2))
    return None


def _expected_amount(ctx: CheckContext, shares: int, fv: Decimal, decimals: int) -> Decimal:
    q = Decimal(1).scaleb(-max(decimals, 0))
    return (Decimal(shares) * fv / Decimal(ctx.config.unit_divisor)).quantize(q, rounding=ROUND_HALF_UP)


@check("SUB-06")
def check_share_arithmetic(ctx: CheckContext) -> None:
    for note in _share_notes(ctx):
        sh = ctx.wb.sheet(note.sheet)
        fv_default = _face_value(ctx, sh, note)
        _caption_amounts(ctx, sh, note)
        _reconciliation(ctx, sh, note, fv_default)
        _double_counted(ctx, sh, note)
        _closing_vs_issued(ctx, sh, note)


def _caption_amounts(ctx: CheckContext, sh: Sheet, note: NoteLoc) -> None:
    """'N equity shares of ₹ X each' captions: N × X ÷ unit = printed amount (CY and PY)."""
    for r in range(note.row, (note.end_row or sh.max_row) + 1):
        if sh.row_hidden(r):
            continue
        cap = ctx.caption(sh, r)
        m = _COUNT_FV.search(cap)
        if not m or not row_numbers(ctx, sh, r):
            continue
        cy_count, fv = _int(m.group(1)), _dec(m.group(2))
        py_m = _PY_COUNT.search(cap)
        py_count = _int(py_m.group(1)) if py_m else cy_count
        if cy_count is None or fv is None:
            continue
        pcols = periods_at(ctx, sh, r)
        for p, count in (("CY", cy_count), ("PY", py_count)):
            col = pcols.get(p)
            if col is None or count is None or not has_number(ctx, sh, r, col):
                continue
            x = loc(ctx, sh, r, col)
            cell = sh.cell(r, col)
            if x.value is None or cell is None:
                continue
            expected = _expected_amount(ctx, count, fv, format_info(sh.number_format(cell)).value_decimals)
            if x.value == expected:
                continue
            ctx.emit(
                "SUB-06", sheet=sh.name, cells=[x.addr], caption=cap, printed=ctx.printed_text(sh, r, col), expected=amt(expected),
                difference=x.value - expected,
                explanation=f"{count:,} shares × ₹{fv} ÷ {ctx.config.unit_divisor:,} = {amt(expected)} ({p}), but the amount prints {amt(x.value)}.",
                evidence=[ev(ctx, x, "Amount")], suggested_fix="Correct the share count in the caption or the amount (shares × face value).",
                period=p, key=f"cap:{r}:{p}",
            )


def _col_kind(ctx: CheckContext, sh: Sheet, row: int, col: int) -> str | None:
    hdr = ctx.column_header(sh, col, row).lower()
    for r in range(row - 1, max(0, row - 8), -1):
        c = sh.cell(r, col)
        if c is not None and isinstance(c.value, str) and c.value.strip():
            hdr = c.value.lower()
            break
    if re.search(r"number|no\.?\s+of\s+shares|shares$|^shares|count", hdr):
        return "shares"
    if re.search(r"amount|₹|rs\.?|inr|lakh|million|crore|value", hdr):
        return "amount"
    if re.search(r"%|percent|holding", hdr):
        return "pct"
    return None


def _reconciliation(ctx: CheckContext, sh: Sheet, note: NoteLoc, fv: Decimal | None) -> None:
    """Reconciliation table: for each row, shares × face value = amount in the same period."""
    if fv is None:
        return
    for t in ctx.tables(sh):
        if not (note.row <= t.r1 <= (note.end_row or sh.max_row)):
            continue
        kinds = {c: _col_kind(ctx, sh, t.r1, c) for c in t.num_cols}
        share_cols = [c for c, k in kinds.items() if k == "shares"]
        amount_cols = [c for c, k in kinds.items() if k == "amount"]
        if not share_cols or not amount_cols:
            continue
        pairs = []
        for sc in share_cols:
            ps = _period(ctx, sh, t.r1, sc)
            cands = [ac for ac in amount_cols if ac > sc]
            if ps is not None:
                same = [ac for ac in amount_cols if _period(ctx, sh, t.r1, ac) == ps]
                cands = same or cands
            if cands:
                pairs.append((sc, min(cands, key=lambda ac: abs(ac - sc)), ps))
        for sc, ac, ps in pairs:
            for r in t.rows:
                if sh.row_hidden(r):
                    continue
                s_cell, a_cell = sh.cell(r, sc), sh.cell(r, ac)
                if s_cell is None or a_cell is None or not s_cell.is_number or not a_cell.is_number:
                    continue
                shares = ctx.displayed(sh, r, sc)
                amount = ctx.displayed(sh, r, ac)
                if shares is None or amount is None:
                    continue
                expected = _expected_amount(ctx, int(shares), fv, format_info(sh.number_format(a_cell)).value_decimals)
                if amount == expected:
                    continue
                x = loc(ctx, sh, r, ac)
                ctx.emit(
                    "SUB-06", sheet=sh.name, cells=[x.addr], caption=ctx.caption(sh, r), printed=ctx.printed_text(sh, r, ac), expected=amt(expected),
                    difference=amount - expected,
                    explanation=f"{int(shares):,} shares × ₹{fv} face value = {amt(expected)}, but the amount prints {amt(amount)}" + (f" ({ps})" if ps else "") + ".",
                    evidence=[ctx.evidence_for(sh, r, sc, "Number of shares"), ev(ctx, x, "Amount")],
                    suggested_fix="Amount must equal number of shares × face value; check whether another class is included or the count is wrong.",
                    period=ps, key=f"rec:{r}:{ac}",
                )


def _double_counted(ctx: CheckContext, sh: Sheet, note: NoteLoc) -> None:
    """A total whose range also contains a cell that merely repeats / subtotals other cells of that range
    (e.g. an interim "Balance as at <date>" row inside the closing share-count SUM)."""
    for r in range(note.row, (note.end_row or sh.max_row) + 1):
        for c in row_numbers(ctx, sh, r):
            cell = sh.cell(r, c)
            if cell is None or cell.formula is None:
                continue
            lf = ctx.linear(sh, cell)
            if lf is None or not lf.is_total:
                continue
            comps: set[tuple[int, int]] = set()
            for term in lf.terms:
                if (ctx.wb.get_sheet(term.sheet) or sh).name != sh.name:
                    comps = set()
                    break
                comps.update(term.area.cells())
            if len(comps) < 3:
                continue
            for rr, cc in sorted(comps):
                sub = sh.cell(rr, cc)
                if sub is None or sub.formula is None or sub.value in (None, 0, 0.0):
                    continue
                slf = ctx.linear(sh, sub)
                if slf is None or not slf.terms or not slf.unit or slf.constant:
                    continue
                inner: set[tuple[int, int]] = set()
                same_sheet = True
                for term in slf.terms:
                    if (ctx.wb.get_sheet(term.sheet) or sh).name != sh.name:
                        same_sheet = False
                    inner.update(term.area.cells())
                if not same_sheet:
                    continue
                inner_nonempty = {(a, b) for a, b in inner if (cc_ := sh.cell(a, b)) is not None and cc_.value not in (None, "", 0, 0.0)}
                if not inner_nonempty or not inner_nonempty <= comps - {(rr, cc)}:
                    continue
                total = ctx.displayed(sh, r, c) or ZERO
                sub_v = ctx.displayed(sh, rr, cc) or ZERO
                x = loc(ctx, sh, r, c)
                ctx.emit(
                    "SUB-06", sheet=sh.name, cells=[x.addr], caption=ctx.caption(sh, r), printed=ctx.printed_text(sh, r, c), expected=amt(total - sub_v),
                    difference=sub_v,
                    explanation=(f"The total (={cell.formula}) adds {sh.name}!{sub.addr} ('{ctx.caption(sh, rr)}', ={sub.formula}) as well as the cells it already "
                                 f"repeats, so it double-counts {amt(sub_v)}."),
                    evidence=[ev(ctx, x, "Total"), ctx.evidence_for(sh, rr, cc, "Counted twice")],
                    suggested_fix="Exclude the interim / subtotal row from the total's range (or sum only the movement rows).",
                    key=f"dbl:{x.addr}",
                )
                break


def _issued_amount(ctx: CheckContext, sh: Sheet, note: NoteLoc) -> dict[str, Loc]:
    """Issued, subscribed and paid-up share capital per period (the note's own amount line)."""
    out: dict[str, Loc] = {}
    for r in range(note.row, (note.end_row or sh.max_row) + 1):
        cap = ctx.caption(sh, r).lower()
        if not _COUNT_FV.search(cap) or "authori" in cap:
            continue
        prev = " ".join(ctx.caption(sh, rr).lower() for rr in range(max(note.row, r - 2), r))
        if "authori" in prev and "issued" not in prev:
            continue
        for p, c in periods_at(ctx, sh, r).items():
            if has_number(ctx, sh, r, c):
                out.setdefault(p, loc(ctx, sh, r, c))
    return out


def _closing_vs_issued(ctx: CheckContext, sh: Sheet, note: NoteLoc) -> None:
    """The reconciliation's closing amount must equal the issued share capital of the same date."""
    issued = _issued_amount(ctx, sh, note)
    if not issued:
        return
    for r in range(note.row, (note.end_row or sh.max_row) + 1):
        if not re.search(r"(at\s+the\s+end|closing|outstanding\s+at\s+the\s+end)", norm_caption(ctx.caption(sh, r))):
            continue
        for c in row_numbers(ctx, sh, r):
            if _col_kind(ctx, sh, r, c) != "amount":
                continue
            p = cell_period(ctx, sh, r, c)
            want = issued.get(p or "")
            x = loc(ctx, sh, r, c)
            if want is None or want.value is None or x.value is None or want.row == r or same_figure(ctx, x, want):
                continue
            ctx.emit(
                "SUB-06", sheet=sh.name, cells=[x.addr], caption=ctx.caption(sh, r), printed=ctx.printed_text(sh, r, c), expected=amt(want.value),
                difference=x.value - want.value,
                explanation=f"The reconciliation's closing share capital ({p}) {amt(x.value)} differs from the issued, subscribed and paid-up capital {amt(want.value)}.",
                evidence=[ev(ctx, x, "Reconciliation closing"), ev(ctx, want, "Issued capital")],
                suggested_fix="Shares outstanding at the end × face value must equal the issued share capital; correct the share count.",
                period=p, key=f"close:{x.addr}",
            )


# =============================================================================== SUB-07


def _total_shares(ctx: CheckContext, sh: Sheet, note: NoteLoc) -> dict[str, Decimal]:
    """Closing number of shares per period (reconciliation 'at the end' row, else the caption count)."""
    out: dict[str, Decimal] = {}
    for r in range(note.row, (note.end_row or sh.max_row) + 1):
        cap = norm_caption(ctx.caption(sh, r))
        if not re.search(r"(at\s+the\s+end|closing|outstanding\s+at\s+the\s+end)", cap):
            continue
        for c in row_numbers(ctx, sh, r):
            if _col_kind(ctx, sh, r, c) != "shares":
                continue
            p = cell_period(ctx, sh, r, c)
            v = ctx.displayed(sh, r, c)
            if p and v is not None and p not in out:
                out[p] = v
    if not out:
        for r in range(note.row, (note.end_row or sh.max_row) + 1):
            m = _COUNT_FV.search(ctx.caption(sh, r))
            if m and re.search(r"issued|subscribed|paid", ctx.caption(sh, r).lower()):
                cy = _int(m.group(1))
                py_m = _PY_COUNT.search(ctx.caption(sh, r))
                if cy:
                    out["CY"] = Decimal(cy)
                if py_m and _int(py_m.group(1)):
                    out["PY"] = Decimal(_int(py_m.group(1)) or 0)
                break
    return out


def _holding_tables(ctx: CheckContext, sh: Sheet, note: NoteLoc) -> list[tuple[str, Table]]:
    out: list[tuple[str, Table]] = []
    for t in ctx.tables(sh):
        if not (note.row <= t.r1 <= (note.end_row or sh.max_row)):
            continue
        hdrs = " ".join((t.headers.get(c) or "").lower() for c in t.num_cols)
        if "%" not in hdrs and "holding" not in hdrs and "percent" not in hdrs:
            continue
        kind = "promoter" if re.search(r"promoter", (t.title or "").lower() + " " + hdrs) else "holders"
        out.append((kind, t))
    return out


@check("SUB-07")
def check_shareholding(ctx: CheckContext) -> None:
    for note in _share_notes(ctx):
        sh = ctx.wb.sheet(note.sheet)
        totals = _total_shares(ctx, sh, note)
        # (holder, period) -> [(loc, table kind)] across the > 5 % holders and promoter tables
        seen: list[tuple[str, str, Loc, str]] = []
        for kind, t in _holding_tables(ctx, sh, note):
            share_cols = [c for c in t.num_cols if _col_kind(ctx, sh, t.r1, c) == "shares"]
            pct_cols = [c for c in t.num_cols if _col_kind(ctx, sh, t.r1, c) == "pct" or _is_pct_col(ctx, sh, t.rows, c)]
            change_cols = [c for c in pct_cols if re.search(r"change", ctx.column_header(sh, c, t.r1).lower())]
            pct_cols = [c for c in pct_cols if c not in change_cols]
            for r in t.rows:
                if sh.row_hidden(r) or is_total_caption(ctx.caption(sh, r)):
                    continue
                holder = norm_caption(ctx.caption(sh, r))
                if not holder:
                    continue
                row_pcts: dict[str, Loc] = {}
                for pc in pct_cols:
                    # a "% holding" column takes the date of the share-count column it sits next to
                    left = max((c for c in share_cols if c < pc), default=None)
                    p = _period(ctx, sh, r, pc) or (_period(ctx, sh, r, left) if left is not None else None) or ("CY" if len(pct_cols) == 1 else None)
                    pct = ctx.displayed(sh, r, pc)
                    if p is None or pct is None:
                        continue
                    x = loc(ctx, sh, r, pc)
                    row_pcts[p] = x
                    twice = False
                    # same holder, other table, same period: same percentage
                    for h2, p2, x2, k2 in seen:
                        if k2 != kind and p2 == p and fuzz.ratio(h2, holder) >= 92 and x2.value is not None and x2.value != pct:
                            twice = True
                            ctx.emit(
                                "SUB-07", sheet=sh.name, cells=[x.addr], caption=ctx.caption(sh, r), printed=ctx.printed_text(sh, r, pc),
                                expected=ctx.printed_text(sh, x2.row, x2.col),
                                explanation=(f"'{ctx.caption(sh, r)}' holds {ctx.printed_text(sh, r, pc)} in the {kind} table but "
                                             f"{ctx.printed_text(sh, x2.row, x2.col)} in the {k2} table ({p})."),
                                evidence=[ev(ctx, x), ev(ctx, x2)], suggested_fix="Compute both percentages on the same basis (shares ÷ total shares of the class).",
                                period=p, key=f"twice:{holder}:{p}",
                            )
                    seen.append((holder, p, x, kind))
                    if twice:
                        continue
                    # % = shares ÷ total shares of the same date
                    sc = next((c for c in share_cols if _period(ctx, sh, r, c) == p), share_cols[0] if len(share_cols) == 1 else None)
                    total = totals.get(p)
                    shares = ctx.displayed(sh, r, sc) if sc is not None else None
                    cell = sh.cell(r, pc)
                    if not total or shares is None or cell is None:
                        continue
                    dec = format_info(sh.number_format(cell)).value_decimals
                    expected = (shares / total).quantize(Decimal(1).scaleb(-dec), rounding=ROUND_HALF_UP)
                    if pct == expected:
                        continue
                    shown = max(dec - 2, 0)
                    ctx.emit(
                        "SUB-07", sheet=sh.name, cells=[x.addr], caption=ctx.caption(sh, r), printed=ctx.printed_text(sh, r, pc),
                        expected=f"{expected * 100:.{shown}f}%", difference=pct - expected,
                        explanation=f"{int(shares):,} shares out of {int(total):,} is {expected * 100:.{shown}f}%, but the table prints {ctx.printed_text(sh, r, pc)} ({p}).",
                        evidence=[ctx.evidence_for(sh, r, sc, "Shares held"), ev(ctx, x, "% holding")] if sc is not None else [ev(ctx, x, "% holding")],
                        suggested_fix="Percentage = shares held ÷ total shares of that class at the same date.",
                        period=p, key=f"pct:{r}:{pc}",
                    )
                # promoter "% change during the year" = CY % − PY %
                for cc in change_cols:
                    ch = ctx.displayed(sh, r, cc)
                    cy = row_pcts.get("CY") or next((x for h, p, x, _k in seen if p == "CY" and fuzz.ratio(h, holder) >= 92), None)
                    py = row_pcts.get("PY") or next((x for h, p, x, _k in seen if p == "PY" and fuzz.ratio(h, holder) >= 92), None)
                    if ch is None or cy is None or py is None or cy.value is None or py.value is None:
                        continue
                    expected = cy.value - py.value
                    if abs(ch - expected) <= Decimal("0.0001"):
                        continue
                    x = loc(ctx, sh, r, cc)
                    ctx.emit(
                        "SUB-07", sheet=sh.name, cells=[x.addr], caption=ctx.caption(sh, r), printed=ctx.printed_text(sh, r, cc),
                        expected=f"{expected * 100:.2f}%", difference=ch - expected,
                        explanation=f"Promoter '{ctx.caption(sh, r)}': % change during the year should be CY % − PY % = {expected * 100:.2f}%.",
                        evidence=[ev(ctx, cy, "CY %"), ev(ctx, py, "PY %"), ev(ctx, x, "% change")],
                        suggested_fix="Compute the % change as the difference of the two holding percentages.", key=f"chg:{r}",
                    )


def _period(ctx: CheckContext, sh: Sheet, row: int, col: int) -> str | None:
    return cell_period(ctx, sh, row, col)


def _is_pct_col(ctx: CheckContext, sh: Sheet, rows: list[int], col: int) -> bool:
    for r in rows:
        c = sh.cell(r, col)
        if c is not None and c.is_number:
            return format_info(sh.number_format(c)).percent > 0
    return False
