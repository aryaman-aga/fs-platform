"""Roll-forward family (02 §6.2): ROLL-01…05.

Movement tables are recognised from their row captions, in either layout:

* *dated*: "As at April 1, 2024 / Additions / Disposals / As at March 31, 2025 /
  Additions / … / As at March 31, 2026" (PPE schedules, SOCE);
* *labelled*: "Opening balance / Add: … / Less: … / Closing balance" with one
  column per period (lease liabilities, gratuity DBO, share reconciliation).

Horizontal rolls (opening | recognised in P&L | in OCI | closing columns, e.g. the
deferred-tax table) are checked row by row. All arithmetic is on displayed values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import TYPE_CHECKING

from fscore.checks.base import CheckContext
from fscore.checks.xref import (
    Loc,
    amt,
    cell_period,
    col_period,
    ev,
    has_number,
    is_linear_of,
    loc,
    rounding_only,
    row_heading_period,
    same_figure,
)
from fscore.engine import formula as F
from fscore.engine.periods import PeriodLabel, parse_period, scope_key
from fscore.engine.statements import norm_caption
from fscore.io.xlsx_reader import Sheet
from fscore.registry import check

if TYPE_CHECKING:
    from fscore.engine.tables import Table

ZERO = Decimal(0)

OPEN_RX = re.compile(
    r"opening\s+balance|balance\s+(at|as\s+at)\s+the\s+(beginning|start)|at\s+the\s+beginning|beginning\s+of\s+the\s+(year|period)"
    r"|as\s+at\s+the\s+beginning|outstanding\s+at\s+the\s+beginning|^opening\b|at\s+the\s+start\s+of"
)
CLOSE_RX = re.compile(
    r"closing\s+balance|balance\s+(at|as\s+at)\s+the\s+end|at\s+the\s+end\s+of|end\s+of\s+the\s+(year|period)"
    r"|as\s+at\s+the\s+end|outstanding\s+at\s+the\s+end|^closing\b"
)
DATED_RX = re.compile(r"^(balance\s+)?(as\s+at|as\s+on|at)\s+\S|^(gross|net)\s+(carrying\s+amount|block)\s+as\s+at|^(opening|closing)\s+balance\s+as\s+at")
DEDUCT_RX = re.compile(
    r"^(less|deduct)\b|disposal|deletion|\bsold\b|written\s+off|write[\s-]?off|utili[sz]|revers|\bpaid\b|payments?\b|repaid|repayment"
    r"|transfer(red)?\s+(out|to)\b|withdraw|redeem|settle|dividend|deduction|derecogni|buy[\s-]?back"
)
NET_MOVE_RX = re.compile(r"addition|increase|\(decrease\)|/\s*\(")
SUBTOTAL_RX = re.compile(r"^total\b|^sub[\s-]*total|total\s+comprehensive")
SECTION_RX = re.compile(r"gross\s+(carrying|block)|accumulated\s+(depreciation|amorti|impairment)|net\s+(carrying|block)|carrying\s+amount|^cost$|deemed\s+cost|^at\s+cost$|impairment")
CHARGE_RX = re.compile(r"charge|depreciation\s+for|amorti[sz]ation\s+for|for\s+the\s+(year|period)$|depreciation$|amorti[sz]ation$")


@dataclass
class Segment:
    sheet: str
    open_row: int
    close_row: int
    moves: list[int]
    cols: list[int]
    section: str = ""
    layout: str = "labelled"  # dated | labelled
    start: date | None = None
    end: date | None = None
    hidden_moves: list[int] = field(default_factory=list)


def _cap(ctx: CheckContext, sh: Sheet, t: Table, row: int) -> str:
    return norm_caption(ctx.caption(sh, row, before_col=min(t.num_cols) if t.num_cols else None))


def _printed_nums(ctx: CheckContext, sh: Sheet, row: int, cols: list[int]) -> list[int]:
    return [c for c in cols if has_number(ctx, sh, row, c) and ctx.is_printed(sh, row, c)]


def _dated(cap: str) -> date | None:
    if not DATED_RX.search(cap):
        return None
    lab = parse_period(cap)
    return lab.end if lab is not None else None


def table_cols(t: Table) -> list[int]:
    return [c for c in t.num_cols if c != t.note_col]


def find_segments(ctx: CheckContext, sh: Sheet, t: Table) -> list[Segment]:
    """Opening → movements → closing sequences inside one table."""
    key = f"segments:{sh.name}:{t.r1}"
    if key in ctx.shared:
        return ctx.shared[key]  # type: ignore[no-any-return]
    cols = table_cols(t)
    segs: list[Segment] = []
    open_row: int | None = None
    open_date: date | None = None
    open_layout = "labelled"
    moves: list[int] = []
    hidden: list[int] = []
    section = norm_caption(t.title)
    r_start = (t.header_rows[-1] + 1) if t.header_rows else max(1, t.r1 - 2)
    for row in range(r_start, t.r2 + 1):
        cap = _cap(ctx, sh, t, row)
        if sh.row_hidden(row):
            if open_row is not None and any(has_number(ctx, sh, row, c) for c in cols):
                hidden.append(row)
            continue
        nums = _printed_nums(ctx, sh, row, cols)
        if not nums:
            if cap and SECTION_RX.search(cap):  # "Gross carrying amount", "Accumulated depreciation" start a new roll
                section = cap
                open_row, open_date, moves, hidden = None, None, [], []
            continue
        d = _dated(cap)
        if d is not None:
            if open_row is not None and moves and (open_date is None or d > open_date):
                segs.append(Segment(sh.name, open_row, row, list(moves), cols, section, "dated", open_date, d, list(hidden)))
            open_row, open_date, open_layout, moves, hidden = row, d, "dated", [], []
            continue
        if OPEN_RX.search(cap) and not CLOSE_RX.search(cap):
            open_row, open_date, open_layout, moves, hidden = row, None, "labelled", [], []
            continue
        if CLOSE_RX.search(cap):
            if open_row is not None and moves:
                segs.append(Segment(sh.name, open_row, row, list(moves), cols, section, open_layout, open_date, None, list(hidden)))
            open_row, open_date, moves, hidden = None, None, [], []
            continue
        if open_row is not None and not SUBTOTAL_RX.search(cap):
            moves.append(row)
    ctx.shared[key] = segs
    return segs


def move_value(cap: str, v: Decimal) -> Decimal:
    """Printed movement as it affects the balance: deductions printed positive are subtracted."""
    if v > 0 and DEDUCT_RX.search(cap) and not (NET_MOVE_RX.search(cap) and not cap.startswith(("less", "deduct"))):
        return -v
    return v


def _segment_period(ctx: CheckContext, sh: Sheet, seg: Segment, col: int) -> str | None:
    if seg.end is not None:
        return scope_key(PeriodLabel(seg.end, None, "point", ""), ctx.config)
    return cell_period(ctx, sh, seg.close_row, col)


def segment_rows_for(ctx: CheckContext, sh: Sheet, t: Table, rx: re.Pattern[str], period: str, *, section_rx: re.Pattern[str] | None = None) -> list[tuple[Segment, int]]:
    """(segment, movement row) pairs whose movement caption matches ``rx`` for a period."""
    out = []
    for seg in find_segments(ctx, sh, t):
        if section_rx is not None and not section_rx.search(seg.section):
            continue
        for r in seg.moves:
            if rx.search(_cap(ctx, sh, t, r)):
                if seg.layout == "dated":
                    if _segment_period(ctx, sh, seg, seg.cols[0]) == period:
                        out.append((seg, r))
                else:
                    out.append((seg, r))
    return out


# =============================================================================== ROLL-01 / ROLL-02


@check("ROLL-01", "ROLL-02", "ROLL-03")
def check_rollforwards(ctx: CheckContext) -> None:
    for sh in ctx.print_sheets:
        for t in ctx.tables(sh):
            segs = find_segments(ctx, sh, t)
            for seg in segs:
                _check_segment(ctx, sh, t, seg)
            _check_continuity(ctx, sh, t, segs)
            _check_net_block(ctx, sh, t)


def _check_segment(ctx: CheckContext, sh: Sheet, t: Table, seg: Segment) -> None:
    for col in seg.cols:
        close_c = sh.cell(seg.close_row, col)
        if close_c is None or not close_c.is_number or not ctx.is_printed(sh, seg.close_row, col):
            continue
        close_v = ctx.displayed(sh, seg.close_row, col)
        if close_v is None:
            continue
        open_v = ctx.displayed(sh, seg.open_row, col)
        open_c = sh.cell(seg.open_row, col)
        if open_v is None:
            if open_c is not None and isinstance(open_c.value, str) and open_c.value.strip() not in ("", "-"):
                continue  # text in the opening column: not a numeric roll
            open_v = ZERO
        total = open_v
        parts = [Loc(sh.name, seg.open_row, col, open_v)]
        signed: list[tuple[Loc, int]] = [(parts[0], 1)]
        evidence = [ctx.evidence_for(sh, seg.open_row, col, "Opening")]
        any_move = False
        for r in seg.moves:
            v = ctx.displayed(sh, r, col)
            if v is None:
                continue
            any_move = True
            mv = move_value(_cap(ctx, sh, t, r), v)
            total += mv
            parts.append(Loc(sh.name, r, col, v))
            signed.append((parts[-1], 1 if mv == v else -1))
            evidence.append(ctx.evidence_for(sh, r, col))
        if not any_move and open_v == ZERO:
            continue
        if total == close_v:
            continue
        closing = Loc(sh.name, seg.close_row, col, close_v)
        lf = ctx.linear(sh, close_c) if close_c.formula else None
        if (not seg.hidden_moves and lf is not None and _within_segment(ctx, sh, lf, seg, col)
                and (abs(total - close_v) <= Decimal("0.05") or (lf.constant and total + lf.constant == close_v))):
            continue  # rounding of a formula total (CAST-01) or a typed constant inside it (PLUG-01/02)
        if any(f.rule_id == "ROLL-04" and f.sheet == sh.name and closing.addr in f.cells for f in ctx.findings):
            continue  # the closing re-uses the current-year formula: reported once as ROLL-04
        if not seg.hidden_moves and rounding_only(ctx, closing, signed):
            continue  # the same balance reached through different rounding paths
        if _sign_convention(ctx, sh, t, seg, col, open_v, close_v, closing):
            continue
        hidden_note = ""
        if seg.hidden_moves:
            hidden_note = f" Hidden row(s) {', '.join(str(r) for r in seg.hidden_moves)} carry movements that do not print."
        hdr = (t.headers.get(col) or "").strip()
        ctx.emit(
            "ROLL-01",
            sheet=sh.name,
            cells=[closing.addr],
            caption=ctx.caption(sh, seg.close_row),
            printed=ctx.printed_text(sh, seg.close_row, col),
            expected=amt(total),
            difference=close_v - total,
            explanation=(
                f"Roll-forward does not add up{(' (' + hdr + ')') if hdr else ''}: opening {amt(open_v)} plus movements gives {amt(total)}, "
                f"but the closing balance prints {amt(close_v)}.{hidden_note}"
            ),
            evidence=[*evidence, ctx.evidence_for(sh, seg.close_row, col, "Closing")],
            suggested_fix="Reconcile the movements with the supporting schedule (FAR / actuarial / lease workings); correct the movement or the closing link.",
            key=f"{seg.open_row}",
        )


def _within_segment(ctx: CheckContext, sh: Sheet, lf: object, seg: Segment, col: int) -> bool:
    """The closing is a unit-coefficient formula over cells of this roll (same column, opening..closing rows)."""
    from fscore.engine.linear import LinearForm

    assert isinstance(lf, LinearForm)
    if not lf.terms or not lf.unit:
        return False
    for t in lf.terms:
        if (ctx.wb.get_sheet(t.sheet) or sh).name != sh.name or t.area.c1 != col or t.area.c2 != col:
            return False
        if t.area.r1 < seg.open_row or t.area.r2 >= seg.close_row:
            return False
    return True


_OCI_ROW = re.compile(r"other\s+comprehensive|\boci\b")
_PL_ROW = re.compile(r"profit\s+(and|&)\s+loss|statement\s+of\s+profit|\bp\s*&\s*l\b")


def _sign_convention(ctx: CheckContext, sh: Sheet, t: Table, seg: Segment, col: int, open_v: Decimal, close_v: Decimal, closing: Loc) -> bool:
    """ROLL-05: a deferred-tax roll that only works with the OCI (or P&L) movement's sign inverted."""
    context = (seg.section + " " + norm_caption(t.title or "")).lower()
    if "deferred tax" not in context:
        return False
    moves: list[tuple[int, Decimal]] = []
    for r in seg.moves:
        v = ctx.displayed(sh, r, col)
        if v is not None:
            moves.append((r, v))
    for rx, label in ((_OCI_ROW, "OCI"), (_PL_ROW, "profit and loss")):
        target = [r for r, _v in moves if rx.search(_cap(ctx, sh, t, r))]
        if len(target) != 1:
            continue
        flipped = open_v + sum(((-v if r == target[0] else move_value(_cap(ctx, sh, t, r), v)) for r, v in moves), ZERO)
        if flipped != close_v:
            continue
        mv = loc(ctx, sh, target[0], col)
        ctx.emit(
            "ROLL-05",
            sheet=sh.name,
            cells=[mv.addr, closing.addr],
            caption=ctx.caption(sh, target[0]),
            printed=ctx.printed_text(sh, target[0], col),
            expected=amt(-(mv.value or ZERO)),
            difference=(mv.value or ZERO) * 2,
            explanation=(f"The deferred-tax roll-forward only adds up if the {label} movement {amt(mv.value)} is taken with the opposite sign: "
                         f"opening {amt(open_v)} + movements as printed ≠ closing {amt(close_v)}."),
            evidence=[ctx.evidence_for(sh, seg.open_row, col, "Opening"), ev(ctx, mv, label), ev(ctx, closing, "Closing")],
            suggested_fix=f"Present the {label} movement with the same sign convention as the other movements ('credit/(charge)').",
            key=f"{seg.open_row}:{col}",
        )
        return True
    return False


def _subheader(text: str) -> str:
    t = norm_caption(text)
    t = re.sub(r"(as\s+at|year\s+ended|for\s+the\s+year\s+ended|period\s+ended)?\s*[a-z]*\s*\d{1,2}(st|nd|rd|th)?,?\s*[a-z]*\s*,?\s*\d{4}", " ", t)
    return " ".join(t.split())


def _check_continuity(ctx: CheckContext, sh: Sheet, t: Table, segs: list[Segment]) -> None:
    """ROLL-02: previous-year closing = current-year opening."""
    labelled = [s for s in segs if s.layout == "labelled"]
    # (a) one roll per period column: CY opening (CY column) vs PY closing (PY column), per sub-header
    for seg in labelled:
        cy_cols = [c for c in seg.cols if col_period(ctx, sh, seg.open_row, c) == "CY"]
        py_cols = [c for c in seg.cols if col_period(ctx, sh, seg.open_row, c) == "PY"]
        for cc in cy_cols:
            sub_c = _subheader(t.headers.get(cc, ""))
            match = [pc for pc in py_cols if _subheader(t.headers.get(pc, "")) == sub_c] or (py_cols if len(cy_cols) == 1 == len(py_cols) else [])
            if len(match) == 1:
                _compare_open_close(ctx, sh, seg.open_row, cc, seg.close_row, match[0], key=f"{seg.open_row}:{cc}")
    # (b) one block per period (rows headed "Year ended …"): CY block opening vs PY block closing, per column
    by_period: dict[str, Segment] = {}
    for seg in labelled:
        if any(col_period(ctx, sh, seg.open_row, c) for c in seg.cols):
            continue
        p = row_heading_period(ctx, sh, seg.open_row)
        if p in ("CY", "PY") and p not in by_period:
            by_period[p] = seg
    if "CY" in by_period and "PY" in by_period:
        cy, py = by_period["CY"], by_period["PY"]
        for col in cy.cols:
            if col in py.cols:
                _compare_open_close(ctx, sh, cy.open_row, col, py.close_row, col, key=f"{cy.open_row}:{col}")
    # (c) dated layout: a closing "As at D" followed later by an opening "As at D + 1 day"
    dated_rows: list[tuple[int, date]] = []
    for row in range(t.r1, t.r2 + 1):
        if sh.row_hidden(row) or not _printed_nums(ctx, sh, row, table_cols(t)):
            continue
        d = _dated(_cap(ctx, sh, t, row))
        if d is not None:
            dated_rows.append((row, d))
    for (r1, d1), (r2, d2) in pairwise(dated_rows):
        if d2 == d1 + timedelta(days=1):
            for col in table_cols(t):
                _compare_open_close(ctx, sh, r2, col, r1, col, key=f"{r2}:{col}", dates=(d2, d1))


def _compare_open_close(ctx: CheckContext, sh: Sheet, open_row: int, open_col: int, close_row: int, close_col: int, *,
                        key: str, dates: tuple[date, date] | None = None) -> None:
    op, cl = loc(ctx, sh, open_row, open_col), loc(ctx, sh, close_row, close_col)
    if op.value is None or cl.value is None or same_figure(ctx, op, cl):
        return
    what = (f"Opening balance on {dates[0]:%d-%b-%Y} ({amt(op.value)}) differs from the closing balance on {dates[1]:%d-%b-%Y} ({amt(cl.value)})."
            if dates else f"Current-year opening {amt(op.value)} differs from the previous-year closing {amt(cl.value)} in the same table.")
    ctx.emit(
        "ROLL-02",
        sheet=sh.name,
        cells=[op.addr],
        caption=ctx.caption(sh, open_row),
        printed=ctx.printed_text(sh, open_row, open_col),
        expected=ctx.printed_text(sh, close_row, close_col),
        difference=op.value - cl.value,
        explanation=what,
        evidence=[ev(ctx, op, "Opening"), ev(ctx, cl, "Previous closing")],
        suggested_fix="Link the current-year opening balance to the previous-year closing balance.",
        period="CY",
        key=key,
    )


def _check_net_block(ctx: CheckContext, sh: Sheet, t: Table) -> None:
    """ROLL-03: net carrying amount = gross − accumulated depreciation for each date."""
    gross: dict[date, int] = {}
    accdep: dict[date, int] = {}
    net: dict[date, int] = {}
    section = ""
    for row in range(t.r1 - 1, t.r2 + 1):
        if sh.row_hidden(row):
            continue
        cap = _cap(ctx, sh, t, row)
        nums = _printed_nums(ctx, sh, row, table_cols(t))
        if cap and not nums:
            if re.search(r"^gross|^cost$|^at\s+cost|deemed\s+cost|gross\s+(carrying|block)", cap):
                section = "gross"
            elif re.search(r"accumulated|^depreciation|^amorti", cap):
                section = "accdep"
            elif re.search(r"net\s+(carrying|block)|^carrying\s+amount|written\s+down", cap):
                section = "net"
            else:
                section = ""  # another sub-table (e.g. "(b) Capital work-in-progress") starts
            continue
        d = _dated(cap)
        if d is None or not nums:
            continue
        target = {"gross": gross, "accdep": accdep, "net": net}.get(section)
        if target is not None:
            target[d] = row
    if not net or not gross or not accdep:
        return
    for d, nrow in net.items():
        if d not in gross or d not in accdep:
            continue
        grow, arow = gross[d], accdep[d]
        for col in table_cols(t):
            n = loc(ctx, sh, nrow, col)
            g = ctx.displayed(sh, grow, col)
            a = ctx.displayed(sh, arow, col)
            if n.value is None or g is None or a is None:
                continue
            expected = g - abs(a)  # accumulated depreciation may print positive or in brackets
            if n.value == expected or rounding_only(ctx, n, [(Loc(sh.name, grow, col, g), 1), (Loc(sh.name, arow, col, a), -1 if a > 0 else 1)]):
                continue
            if is_linear_of(ctx, n, [Loc(sh.name, grow, col, g), Loc(sh.name, arow, col, a)]) and abs(n.value - expected) <= Decimal("0.05"):
                continue
            ctx.emit(
                "ROLL-03",
                sheet=sh.name,
                cells=[n.addr],
                caption=ctx.caption(sh, nrow),
                printed=ctx.printed_text(sh, nrow, col),
                expected=amt(expected),
                difference=n.value - expected,
                explanation=f"Net carrying amount as at {d:%d-%b-%Y} prints {amt(n.value)}; gross {amt(g)} less accumulated depreciation {amt(abs(a))} is {amt(expected)}.",
                evidence=[ctx.evidence_for(sh, grow, col, "Gross"), ctx.evidence_for(sh, arow, col, "Accumulated depreciation"), ev(ctx, n, "Net")],
                suggested_fix="Compute the net block as gross carrying amount minus accumulated depreciation.",
                key=f"{d.isoformat()}:{col}",
            )


# =============================================================================== ROLL-04


@check("ROLL-04")
def check_py_formulas(ctx: CheckContext) -> None:
    """Previous-year columns must not reference current-year cells (re-used CY formulas)."""
    cache: dict[tuple[str, int, int], str | None] = {}

    def key_of(sheet: Sheet, row: int, col: int) -> str | None:
        k = (sheet.name, row, col)
        if k not in cache:
            cache[k] = col_period(ctx, sheet, row, col)
        return cache[k]

    for sh in ctx.print_sheets:
        for cell in sh.iter_cells():
            if cell.formula is None or not ctx.is_printed(sh, cell.row, cell.col):
                continue
            mine = key_of(sh, cell.row, cell.col)
            if mine is None or not mine.endswith("PY"):
                continue
            node = ctx.parsed(sh, cell)
            if node is None:
                continue
            for ref in F.references(node):
                p = ref.parts
                if p.kind not in ("cell", "range", "cols") or p.c1 != p.c2:
                    continue
                target = ctx.wb.get_sheet(ref.sheet) if ref.sheet else sh
                if target is None:
                    continue
                look_row = target.max_row if p.kind == "cols" else p.r1
                theirs = key_of(target, look_row, p.c1)
                if theirs is None or not theirs.endswith("CY"):
                    continue
                x = loc(ctx, sh, cell.row, cell.col)
                ctx.emit(
                    "ROLL-04",
                    sheet=sh.name,
                    cells=[x.addr],
                    caption=ctx.caption(sh, cell.row),
                    printed=ctx.printed_text(sh, cell.row, cell.col),
                    explanation=f"The previous-year figure at {sh.name}!{x.addr} is calculated from current-year cell {ref.text} (=" + cell.formula + ").",
                    evidence=[ev(ctx, x, "Previous-year cell")],
                    suggested_fix="Point the comparative at the previous-year column (or type the signed prior-year figure).",
                    period=mine,
                    key=ref.text,
                )
                break


# =============================================================================== ROLL-05 (and horizontal rolls)

_H_OPEN = re.compile(r"opening|beginning|as\s+at\s+(april|1st\s+april)")
_H_CLOSE = re.compile(r"closing|end\s+of")
_H_PL = re.compile(r"profit|loss|p\s*&\s*l|statement of")
_H_OCI = re.compile(r"\boci\b|other\s+comprehensive")


@check("ROLL-05")
def check_horizontal_rolls(ctx: CheckContext) -> None:
    """Opening | movements | closing across columns (deferred-tax tables): flag inverted OCI signs."""
    for sh in ctx.print_sheets:
        for t in ctx.tables(sh):
            cols = table_cols(t)
            if len(cols) < 3:
                continue
            hdrs = {c: (t.headers.get(c) or "").lower() for c in cols}
            dates = {}
            for c in cols:
                lab = parse_period(hdrs[c])
                if lab is not None and lab.end is not None:
                    dates[c] = lab.end
            open_col = next((c for c in cols if _H_OPEN.search(hdrs[c])), None)
            close_col = next((c for c in reversed(cols) if _H_CLOSE.search(hdrs[c])), None)
            if (open_col is None or close_col is None) and len(dates) >= 2:
                ordered = sorted(dates.items(), key=lambda kv: kv[1])
                open_col = open_col or ordered[0][0]
                close_col = close_col or ordered[-1][0]
            if open_col is None or close_col is None or close_col <= open_col:
                continue
            moves = [c for c in cols if open_col < c < close_col]
            if not moves:
                continue
            oci_cols = [c for c in moves if _H_OCI.search(hdrs[c])]
            pl_cols = [c for c in moves if _H_PL.search(hdrs[c]) and c not in oci_cols]
            is_dt = bool(re.search(r"deferred\s+tax", (t.title or "").lower()))
            for row in t.rows:
                if sh.row_hidden(row):
                    continue
                o, cl = ctx.displayed(sh, row, open_col), ctx.displayed(sh, row, close_col)
                if cl is None:
                    continue
                o = o or ZERO
                vals = {c: (ctx.displayed(sh, row, c) or ZERO) for c in moves}
                plain = o + sum(vals.values(), ZERO)
                if plain == cl:
                    continue
                inv_oci = o + sum((-v if c in oci_cols else v) for c, v in vals.items())
                inv_pl = o + sum((-v if c in pl_cols else v) for c, v in vals.items())
                x = loc(ctx, sh, row, close_col)
                parts = [Loc(sh.name, row, open_col, o)] + [Loc(sh.name, row, c, v) for c, v in vals.items()]
                evidence = [ctx.evidence_for(sh, row, c, (t.headers.get(c) or "").strip() or None) for c in [open_col, *moves, close_col]]
                if (oci_cols and inv_oci == cl) or (pl_cols and inv_pl == cl):
                    which = "OCI" if (oci_cols and inv_oci == cl) else "profit and loss"
                    ctx.emit(
                        "ROLL-05",
                        sheet=sh.name,
                        cells=[x.addr],
                        caption=ctx.caption(sh, row),
                        printed=ctx.printed_text(sh, row, close_col),
                        expected=amt(plain),
                        difference=cl - plain,
                        explanation=(
                            f"The {'deferred-tax ' if is_dt else ''}row only rolls forward if the {which} column's sign is inverted: "
                            f"opening + movements as printed = {amt(plain)}, closing prints {amt(cl)}."
                        ),
                        evidence=evidence,
                        suggested_fix=f"Present the {which} column with one sign convention (e.g. '(charged)/credited') consistent with the opening and closing balances.",
                        key=f"{row}",
                    )
                    continue
                if (is_linear_of(ctx, x, parts) and abs(plain - cl) <= Decimal("0.05")) or rounding_only(ctx, x, [(q, 1) for q in parts]):
                    continue
                ctx.emit(
                    "ROLL-01",
                    sheet=sh.name,
                    cells=[x.addr],
                    caption=ctx.caption(sh, row),
                    printed=ctx.printed_text(sh, row, close_col),
                    expected=amt(plain),
                    difference=cl - plain,
                    explanation=f"Opening {amt(o)} plus the movements across the row gives {amt(plain)}, but the closing column prints {amt(cl)}.",
                    evidence=evidence,
                    suggested_fix="Reconcile the row's movements with the supporting working.",
                    key=f"h{row}",
                )
