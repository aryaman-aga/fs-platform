"""BAL-01 Balance Sheet tallies on print; BAL-02 cash-flow closing cash = Balance Sheet cash.

Gaps up to the balancing threshold (default 0.20, control C3) are rounding and
are solved by the fix engine through the designated balancing lines. Larger
gaps are reported for investigation; a 0.30–0.40 cash-flow gap usually means
the exchange difference on cash is missing and is never plugged.
"""

from __future__ import annotations

from decimal import Decimal

from fscore.checks.base import CheckContext, fmt_amount
from fscore.engine.refs import index_to_col, make_addr
from fscore.engine.statements import LineLoc
from fscore.models import Certainty, FixKind
from fscore.registry import check


def _row_number_cols(ctx: CheckContext, loc: LineLoc) -> list[int]:
    sh = ctx.wb.sheet(loc.sheet)
    return [c.col for c in ctx.iter_printed_numbers(sh) if c.row == loc.row]


def bs_gaps(ctx: CheckContext) -> list[tuple[int, str | None, Decimal, Decimal, Decimal]]:
    """[(column, scope key, total assets, total E&L, gap)] for every printed BS column."""
    sm = ctx.statements
    ta, tel = sm.line("total_assets"), sm.line("total_eq_liab")
    if ta is None or tel is None or ta.sheet != tel.sheet:
        return []
    sh = ctx.wb.sheet(ta.sheet)
    by_col = {v: k for k, v in ta.cols.items()}
    out = []
    for col in sorted(set(_row_number_cols(ctx, ta)) & set(_row_number_cols(ctx, tel))):
        a = ctx.displayed(sh, ta.row, col)
        b = ctx.displayed(sh, tel.row, col)
        if a is None or b is None:
            continue
        out.append((col, by_col.get(col), a, b, a - b))
    return out


def cf_gaps(ctx: CheckContext) -> list[tuple[str, int, int, Decimal, Decimal, Decimal]]:
    """[(scope key, cf column, bs column, cf closing, bs cash, gap)]."""
    sm = ctx.statements
    cf, cash = sm.line("cf_closing"), sm.line("cash")
    if cf is None or cash is None:
        return []
    cfs, bss = ctx.wb.sheet(cf.sheet), ctx.wb.sheet(cash.sheet)
    out = []
    for key, ccol in cf.cols.items():
        bcol = cash.cols.get(key)
        if bcol is None:
            continue
        a = ctx.displayed(cfs, cf.row, ccol)
        b = ctx.displayed(bss, cash.row, bcol)
        if a is None or b is None:
            continue
        out.append((key, ccol, bcol, a, b, a - b))
    return out


@check("BAL-01", order=30)
def bal_01(ctx: CheckContext) -> None:
    thr = ctx.config.policy.balancing_threshold
    sm = ctx.statements
    ta, tel = sm.line("total_assets"), sm.line("total_eq_liab")
    for col, key, a, b, gap in bs_gaps(ctx):
        if gap == 0:
            continue
        assert ta is not None
        assert tel is not None
        rounding = abs(gap) <= thr
        ctx.emit(
            "BAL-01",
            sheet=ta.sheet,
            cells=[make_addr(ta.row, col), make_addr(tel.row, col)],
            caption=f"Total assets vs total equity and liabilities ({key or index_to_col(col)})",
            printed=fmt_amount(a),
            expected=fmt_amount(b),
            difference=gap,
            period=key,
            fix_kind=FixKind.AUTO if rounding else FixKind.MANUAL,
            certainty=Certainty.CERTAIN,
            explanation=(
                f"The Balance Sheet does not tally on print in column {index_to_col(col)}"
                f"{' (' + key + ')' if key else ''}: total assets {fmt_amount(a)} vs total equity and liabilities "
                f"{fmt_amount(b)} (gap {fmt_amount(gap)})."
                + (" The gap is rounding-sized." if rounding else " The gap exceeds the rounding threshold: investigate, do not balance.")
            ),
            suggested_fix=(
                "After all totals cast, absorb the rounding gap through the designated balancing line (Other / Miscellaneous expenses)."
                if rounding else "Investigate the difference (missing line, translation, stale link); it may not be balanced."
            ),
            key=f"col{col}",
        )


@check("BAL-02", order=31)
def bal_02(ctx: CheckContext) -> None:
    thr = ctx.config.policy.balancing_threshold
    cf = ctx.statements.line("cf_closing")
    cash = ctx.statements.line("cash")
    for key, ccol, bcol, a, b, gap in cf_gaps(ctx):
        if gap == 0:
            continue
        assert cf is not None
        assert cash is not None
        fx_like = Decimal("0.30") <= abs(gap) <= Decimal("0.40")
        rounding = abs(gap) <= thr
        if rounding:
            advice = "Rounding-sized: solve through '(Decrease)/increase in other current liabilities' after all totals cast."
        elif fx_like:
            advice = "This size of gap usually means the exchange difference on cash and cash equivalents is missing: report, never plug."
        else:
            advice = "Investigate: the cash flow does not explain the movement in cash."
        ctx.emit(
            "BAL-02",
            sheet=cf.sheet,
            cells=[make_addr(cf.row, ccol)],
            caption=f"Cash flow closing cash vs Balance Sheet cash ({key})",
            printed=fmt_amount(a),
            expected=fmt_amount(b),
            difference=gap,
            period=key,
            fix_kind=FixKind.AUTO if rounding else FixKind.MANUAL,
            explanation=f"Closing cash per the cash flow {fmt_amount(a)} ≠ cash and cash equivalents on the Balance Sheet {fmt_amount(b)} "
                        f"({cash.sheet}!{make_addr(cash.row, bcol)}), gap {fmt_amount(gap)}. {advice}",
            suggested_fix=advice,
            key=key,
        )
