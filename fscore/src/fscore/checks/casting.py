"""CAST-01 vertical / linear casting and CAST-03 missing total cells (02 §6.1).

Casting is on *printed* values: a total must equal the sum of its components
as they appear on paper, each rendered through its own number format.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from fscore.checks.base import CheckContext, fmt_amount
from fscore.engine.display import excel_round, format_info, is_date_format
from fscore.engine.linear import LinearForm
from fscore.engine.refs import make_addr
from fscore.io.xlsx_reader import Cell, Sheet
from fscore.models import Certainty, Evidence
from fscore.registry import check

MAX_EVIDENCE = 40


@dataclass
class Component:
    sheet: str
    row: int
    col: int
    coef: Decimal
    displayed: Decimal | None
    printed: bool
    error: bool


def components(ctx: CheckContext, lf: LinearForm) -> list[Component] | None:
    """Expand a linear form into its component cells (None if a component sheet is missing)."""
    out: list[Component] = []
    for term in lf.terms:
        sh = ctx.wb.get_sheet(term.sheet)
        if sh is None:
            return None
        a = term.area
        r2 = min(a.r2, max(sh.max_row, a.r1))
        c2 = min(a.c2, max(sh.max_col, a.c1))
        for r in range(a.r1, r2 + 1):
            for c in range(a.c1, c2 + 1):
                cell = sh.cell(r, c)
                if cell is None or cell.value is None:
                    if term.is_cell:
                        out.append(Component(sh.name, r, c, term.coef, Decimal(0), ctx.is_printed(sh, r, c), False))
                    continue
                if cell.is_error:
                    out.append(Component(sh.name, r, c, term.coef, None, ctx.is_printed(sh, r, c), True))
                    continue
                if not cell.is_number:
                    continue  # text inside a SUM range counts as zero
                d = ctx.displayed(sh, r, c)
                if term.rounded is not None and d is not None:
                    d = excel_round(cell.value, term.rounded)
                out.append(Component(sh.name, r, c, term.coef, d, ctx.is_printed(sh, r, c), False))
    return out


def printed_sum(comps: list[Component]) -> Decimal | None:
    total = Decimal(0)
    for c in comps:
        if c.error or c.displayed is None:
            return None
        total += c.coef * c.displayed
    return total


def is_balancing_cell(ctx: CheckContext, sheet: Sheet, cell: Cell) -> bool:
    """The configured balancing lines (C3) legitimately carry a visible '+adj'."""
    import re

    pol = ctx.config.policy.balancing
    cap = ctx.caption(sheet, cell.row)
    for target in (pol.balance_sheet, pol.cash_flow):
        if target.sheet and target.sheet.strip().lower() != sheet.name.strip().lower():
            continue
        if cap and re.search(target.caption, cap, re.I):
            return True
    return False


def casting_failures(ctx: CheckContext, sheet: Sheet) -> list[tuple[Cell, LinearForm, list[Component], Decimal, Decimal]]:
    """(cell, linear form, components, printed total, printed component sum) for totals that do not cast."""
    out = []
    for cell in ctx.iter_printed_numbers(sheet):
        if cell.formula is None:
            continue
        lf = ctx.linear(sheet, cell)
        if lf is None or not lf.is_total:
            continue
        fmt = sheet.number_format(cell)
        if is_date_format(fmt):
            continue
        comps = components(ctx, lf)
        if not comps:
            continue
        expected = printed_sum(comps)
        total = ctx.displayed(sheet, cell.row, cell.col)
        if expected is None or total is None:
            continue
        if lf.constant and abs(lf.constant) <= ctx.config.policy.balancing_threshold and is_balancing_cell(ctx, sheet, cell):
            expected += lf.constant  # the designated line's visible balancing adjustment (C3) is part of the figure
        dec = format_info(fmt).value_decimals
        exp_r = excel_round(expected, dec)
        if exp_r != total:
            out.append((cell, lf, comps, total, expected))
    return out


@check("CAST-01", order=20)
def cast_01(ctx: CheckContext) -> None:
    for sheet in ctx.print_sheets:
        for cell, _lf, comps, total, expected in casting_failures(ctx, sheet):
            diff = total - expected
            ev = [
                Evidence(
                    label=(("+ " if c.coef > 0 else "− ") + (ctx.caption(ctx.wb.sheet(c.sheet), c.row) or make_addr(c.row, c.col)))[:120],
                    ref=ctx.evidence_for(c.sheet, c.row, c.col).ref,
                    printed=ctx.printed_text(ctx.wb.sheet(c.sheet), c.row, c.col),
                    value=c.displayed,
                    note=None if c.printed else "not printed (hidden or outside the print area)",
                )
                for c in comps[:MAX_EVIDENCE]
            ]
            cap = ctx.caption(sheet, cell.row)
            dec = max(format_info(sheet.number_format(cell)).decimals, 2)
            ctx.emit(
                "CAST-01",
                sheet=sheet.name,
                cells=[cell.addr],
                caption=cap,
                printed=ctx.printed_text(sheet, cell.row, cell.col),
                expected=fmt_amount(expected, dec),
                difference=diff,
                evidence=ev,
                explanation=(
                    f"Printed total {fmt_amount(total, dec)} does not equal the sum of its printed components "
                    f"{fmt_amount(expected, dec)} (difference {fmt_amount(diff, dec)})."
                ),
                suggested_fix="Rewrite the total so it adds the displayed components (ROUND/SUMPRODUCT(ROUND())); line items are not changed.",
                key=cell.addr,
                certainty=Certainty.CERTAIN if all(c.printed for c in comps) else Certainty.LIKELY,
            )


@check("CAST-03", order=25)
def cast_03(ctx: CheckContext) -> None:
    """A total row has blank cells in columns where the block above holds numbers."""
    for sheet in ctx.print_sheets:
        for table in ctx.tables(sheet):
            if not table.total_rows or len(table.num_cols) < 2:
                continue
            prev = table.r1 - 1
            for tr in sorted(table.total_rows):
                block_rows = [r for r in table.rows if prev < r < tr]
                prev = tr
                if len(block_rows) < 2:
                    continue
                filled = []
                missing = []
                for col in table.num_cols:
                    if col == table.note_col:
                        continue
                    vals = [ctx.displayed(sheet, r, col) for r in block_rows]
                    nonzero = [v for v in vals if v not in (None, Decimal(0))]
                    tcell = sheet.cell(tr, col)
                    blank = tcell is None or tcell.value is None or (isinstance(tcell.value, str) and not tcell.value.strip())
                    if blank and len(nonzero) >= 2:
                        missing.append(col)
                    elif not blank:
                        filled.append(col)
                if missing and filled:
                    for col in missing:
                        ctx.emit(
                            "CAST-03",
                            sheet=sheet.name,
                            cells=[make_addr(tr, col)],
                            caption=ctx.caption(sheet, tr),
                            explanation=(
                                f"Total row '{ctx.caption(sheet, tr) or make_addr(tr, 1)}' has no total in column "
                                f"{make_addr(tr, col).rstrip('0123456789')} although the rows above hold figures."
                            ),
                            suggested_fix="Add the missing total cell (same formula as the adjacent total, translated to this column).",
                            key=make_addr(tr, col),
                        )
