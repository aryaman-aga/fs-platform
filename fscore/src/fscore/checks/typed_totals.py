"""TYPED-01 hard-typed totals (02 §6.1): total rows holding constants instead of formulas."""

from __future__ import annotations

from decimal import Decimal

from fscore.checks.base import CheckContext, fmt_amount
from fscore.engine.refs import make_addr
from fscore.models import Certainty, Severity
from fscore.registry import check


@check("TYPED-01", order=26)
def typed_totals(ctx: CheckContext) -> None:
    for sheet in ctx.print_sheets:
        for table in ctx.tables(sheet):
            prev = table.r1 - 1
            for tr in sorted(table.total_rows):
                block = [r for r in table.rows if prev < r < tr]
                prev = tr
                if len(block) < 2:
                    continue
                for col in table.num_cols:
                    if col == table.note_col:
                        continue
                    tcell = sheet.cell(tr, col)
                    if tcell is None or not tcell.is_number or tcell.formula is not None:
                        continue
                    if not ctx.is_printed(sheet, tr, col):
                        continue
                    comps = [sheet.cell(r, col) for r in block]
                    nums = [c for c in comps if c is not None and c.is_number]
                    if len(nums) < 2 or all(ctx.displayed(sheet, c.row, col) in (None, Decimal(0)) for c in nums):
                        continue
                    formula_comps = sum(1 for c in nums if c.formula is not None)
                    s = sum(((ctx.displayed(sheet, c.row, col) or Decimal(0)) for c in nums), Decimal(0))
                    total = ctx.displayed(sheet, tr, col) or Decimal(0)
                    linked = formula_comps > 0
                    ctx.emit(
                        "TYPED-01",
                        sheet=sheet.name,
                        cells=[make_addr(tr, col)],
                        caption=ctx.caption(sheet, tr),
                        printed=ctx.printed_text(sheet, tr, col),
                        expected=fmt_amount(s),
                        difference=total - s,
                        severity=Severity.HIGH if linked else Severity.LOW,
                        certainty=Certainty.LIKELY if linked else Certainty.REVIEW,
                        explanation=(
                            f"Total '{ctx.caption(sheet, tr) or tr}' is a typed number ({ctx.printed_text(sheet, tr, col)})"
                            + (" while its components are formulas" if linked else " in a typed block")
                            + f"; the printed components add to {fmt_amount(s)}."
                        ),
                        suggested_fix=f"Replace the typed total with =SUM({make_addr(block[0], col)}:{make_addr(block[-1], col)}).",
                        key=make_addr(tr, col),
                    )
