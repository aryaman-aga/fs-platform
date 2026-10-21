"""CAST-02 cross-casting: a 'Total' column must equal the sum of the printed component
columns of its table, whether the total column is computed across or downward.
"""

from __future__ import annotations

from decimal import Decimal

from fscore.checks.base import CheckContext, fmt_amount
from fscore.engine.display import excel_round, format_info
from fscore.engine.refs import index_to_col, make_addr
from fscore.engine.tables import Table
from fscore.io.xlsx_reader import Sheet
from fscore.models import Evidence
from fscore.registry import check


def component_columns(table: Table, total_col: int) -> list[int]:
    """Numeric columns between the previous total column (or the captions) and this total column."""
    prev_totals = [c for c in table.total_cols if c < total_col]
    left = max(prev_totals) if prev_totals else table.caption_col
    # a table of "classes + Total" sometimes has a period / note column in front: skip note columns
    return [c for c in table.num_cols if left < c < total_col and c != table.note_col and c not in table.total_cols]


def cross_failures(ctx: CheckContext, sheet: Sheet, table: Table) -> list[tuple[int, int, list[int], Decimal, Decimal]]:
    out = []
    for tcol in table.total_cols:
        comps = component_columns(table, tcol)
        if len(comps) < 2:
            continue
        for r in table.rows:
            tcell = sheet.cell(r, tcol)
            if tcell is None or not tcell.is_number or not ctx.is_printed(sheet, r, tcol):
                continue
            total = ctx.displayed(sheet, r, tcol)
            if total is None:
                continue
            s = Decimal(0)
            any_val = False
            for c in comps:
                v = ctx.displayed(sheet, r, c)
                if v is not None:
                    s += v
                    any_val = True
            if not any_val:
                continue
            dec = format_info(sheet.number_format(tcell)).value_decimals
            if excel_round(s, dec) != total:
                out.append((r, tcol, comps, total, s))
    return out


@check("CAST-02", order=21)
def cast_02(ctx: CheckContext) -> None:
    for sheet in ctx.print_sheets:
        for table in ctx.tables(sheet):
            if not table.total_cols:
                continue
            for r, tcol, comps, total, s in cross_failures(ctx, sheet, table):
                diff = total - s
                ev = [
                    Evidence(label=(table.headers.get(c) or index_to_col(c))[:80], ref=ctx.evidence_for(sheet, r, c).ref,
                             printed=ctx.printed_text(sheet, r, c), value=ctx.displayed(sheet, r, c))
                    for c in comps
                ]
                ctx.emit(
                    "CAST-02",
                    sheet=sheet.name,
                    cells=[make_addr(r, tcol)],
                    caption=ctx.caption(sheet, r),
                    printed=ctx.printed_text(sheet, r, tcol),
                    expected=fmt_amount(s),
                    difference=diff,
                    evidence=ev,
                    explanation=(
                        f"Row '{ctx.caption(sheet, r) or r}': the '{table.headers.get(tcol, 'Total')}' column prints {fmt_amount(total)} "
                        f"but the printed columns add to {fmt_amount(s)} (difference {fmt_amount(diff)})."
                    ),
                    suggested_fix="Make the total column add the displayed component columns; where a cell is a total both ways, review the line (two-way casting).",
                    key=make_addr(r, tcol),
                )
