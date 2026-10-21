"""PLUG-01 typed rounding plugs and PLUG-02 larger typed adjustments (02 §6.1).

A plug is a typed constant added to or subtracted from cell references inside a
formula (``SUM(F10:F20)-0.01``, ``J106/100000+0.01``, ``'Note'!G23-0.01``).
Plugs are reported even when the total currently casts. The designated
balancing lines (control C3) carry visible '+adj' formulas by policy and are
reported as information instead.
"""

from __future__ import annotations

from decimal import Decimal

from fscore.checks.base import CheckContext, fmt_amount
from fscore.checks.casting import is_balancing_cell
from fscore.engine.display import format_info
from fscore.models import Certainty, Severity
from fscore.registry import check, get_rule


def plug_bounds(ctx: CheckContext) -> tuple[Decimal, Decimal]:
    params = get_rule("PLUG-01").params
    auto = Decimal(str(params.get("auto_remove_below", "0.10")))
    thr = ctx.config.policy.plug_threshold
    return auto, thr


@check("PLUG-01", "PLUG-02", order=22)
def plugs(ctx: CheckContext) -> None:
    auto, thr = plug_bounds(ctx)
    for sheet in ctx.print_sheets:
        for cell in sheet.iter_cells():
            if cell.formula is None:
                continue
            lf = ctx.linear(sheet, cell)
            if lf is None or not lf.terms or lf.constant == 0:
                continue
            c = lf.constant
            fmt = sheet.number_format(cell)
            info = format_info(fmt)
            if info.kind == "percent" and c in (Decimal(1), Decimal(-1), Decimal(100)):
                continue  # complements such as =1-C5 on percentage rows
            printed = ctx.is_printed(sheet, cell.row, cell.col)
            cap = ctx.caption(sheet, cell.row)
            if is_balancing_cell(ctx, sheet, cell):
                continue  # visible balancing adjustment on the designated line (C3), re-solved by every fix run
            in_total = lf.is_total
            dec = max(info.decimals, 2)
            if abs(c) < auto:
                ctx.emit(
                    "PLUG-01",
                    sheet=sheet.name,
                    cells=[cell.addr],
                    caption=cap,
                    printed=ctx.printed_text(sheet, cell.row, cell.col) if printed else None,
                    severity=Severity.HIGH if (in_total or printed) else Severity.MEDIUM,
                    difference=c,
                    explanation=(
                        f"Formula ={cell.formula} contains a typed rounding plug of {fmt_amount(c, dec)}"
                        + (" inside a total" if in_total else "")
                        + "; it hides the real casting difference."
                    ),
                    suggested_fix="Remove the plug and let the totals-only rewrite cast the total on displayed values.",
                    key=cell.addr,
                )
            elif abs(c) < thr or in_total or lf.is_link or lf.is_scaled_link:
                ctx.emit(
                    "PLUG-02",
                    sheet=sheet.name,
                    cells=[cell.addr],
                    caption=cap,
                    printed=ctx.printed_text(sheet, cell.row, cell.col) if printed else None,
                    certainty=Certainty.REVIEW,
                    difference=c,
                    explanation=(
                        f"Formula ={cell.formula} contains a typed adjustment of {fmt_amount(c, dec)}. "
                        "Adjustments of this size are kept and reported for investigation, not removed automatically."
                    ),
                    suggested_fix="Trace the adjustment to its source (schedule / note) and replace it with a link, or document it.",
                    key=cell.addr,
                )
