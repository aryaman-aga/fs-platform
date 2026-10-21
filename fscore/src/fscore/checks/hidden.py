"""HID-01 hidden contributions to printed totals; HID-02 invisible content and errors in the print area."""

from __future__ import annotations

from decimal import Decimal

from fscore.checks.base import CheckContext, fmt_amount
from fscore.checks.casting import components
from fscore.engine.display import format_info
from fscore.engine.formula import ERRORS
from fscore.engine.refs import make_addr
from fscore.models import Evidence, Severity
from fscore.registry import check


@check("HID-01", order=23)
def hid_01(ctx: CheckContext) -> None:
    for sheet in ctx.print_sheets:
        for cell in ctx.iter_printed_numbers(sheet):
            if cell.formula is None:
                continue
            lf = ctx.linear(sheet, cell)
            if lf is None or not lf.is_total:
                continue
            comps = components(ctx, lf)
            if not comps:
                continue
            hidden = [
                c for c in comps
                if not c.printed and ctx.is_print_sheet(c.sheet) and c.displayed not in (None, Decimal(0))
            ]
            if not hidden:
                continue
            visible_sum = sum((c.coef * (c.displayed or Decimal(0)) for c in comps if c.printed), Decimal(0))
            total = ctx.displayed(sheet, cell.row, cell.col) or Decimal(0)
            ev = [
                Evidence(label=ctx.caption(ctx.wb.sheet(c.sheet), c.row) or make_addr(c.row, c.col),
                         ref=ctx.evidence_for(c.sheet, c.row, c.col).ref, value=c.displayed,
                         printed=ctx.printed_text(ctx.wb.sheet(c.sheet), c.row, c.col), note="hidden / outside print area")
                for c in hidden[:30]
            ]
            ctx.emit(
                "HID-01",
                sheet=sheet.name,
                cells=[cell.addr],
                caption=ctx.caption(sheet, cell.row),
                printed=ctx.printed_text(sheet, cell.row, cell.col),
                expected=fmt_amount(visible_sum),
                difference=total - visible_sum,
                evidence=ev,
                explanation=(
                    f"The printed total {fmt_amount(total)} includes {len(hidden)} hidden / non-printed cell(s) with values; "
                    f"the visible lines add to {fmt_amount(visible_sum)}."
                ),
                suggested_fix="Unhide and disclose the rows, move the amounts to visible lines, or remove them from the total after review.",
                key=cell.addr,
            )


@check("HID-02", order=24)
def hid_02(ctx: CheckContext) -> None:
    for sheet in ctx.print_sheets:
        narrow = {c for c, w in sheet.col_widths.items() if w < 0.75 and not sheet.col_hidden(c)}
        for cell in sheet.iter_cells():
            empty = cell.value is None or (isinstance(cell.value, str) and not cell.value.strip())
            ref_formula = bool(cell.formula and "#REF!" in cell.formula.upper())
            if (empty and not ref_formula) or not sheet.in_print_area(cell.row, cell.col):
                continue
            hidden_pos = sheet.is_hidden(cell.row, cell.col)
            # (d) error values / #REF! formulas anywhere in the print area, hidden rows included
            is_err = cell.is_error or (isinstance(cell.value, str) and cell.value.strip().upper() in ERRORS)
            if is_err or ref_formula:
                ctx.emit(
                    "HID-02",
                    sheet=sheet.name,
                    cells=[cell.addr],
                    caption=ctx.caption(sheet, cell.row),
                    printed=str(cell.value) if cell.value is not None else None,
                    severity=Severity.MEDIUM if hidden_pos else Severity.HIGH,
                    explanation=(
                        f"{'Hidden' if hidden_pos else 'Printed'} cell holds an error"
                        + (f" ({cell.value})" if is_err else "") + (" and a #REF! formula" if ref_formula else "")
                        + (": it would print if the row/column is unhidden." if hidden_pos else ".")
                    ),
                    suggested_fix="Repair the broken reference or clear the cell.",
                    key=cell.addr + ":err",
                    tags=["error"],
                )
                continue
            if hidden_pos:
                continue
            fmt = sheet.number_format(cell)
            if format_info(fmt).hidden:
                ctx.emit(
                    "HID-02", sheet=sheet.name, cells=[cell.addr], caption=ctx.caption(sheet, cell.row),
                    explanation="Cell in the print area uses a ';;;' number format: its value is invisible today and prints if the format changes.",
                    suggested_fix="Clear the working figure or move it outside the print area.", key=cell.addr + ":fmt",
                )
                continue
            if sheet.font_invisible(cell):
                what = f"number {ctx.wb.render_cell(cell).clean}" if cell.is_number else "text"
                ctx.emit(
                    "HID-02", sheet=sheet.name, cells=[cell.addr], caption=ctx.caption(sheet, cell.row),
                    printed=ctx.wb.render_cell(cell).clean,
                    explanation=f"A {what} sits in the print area in white / no-contrast font: invisible today, printed if formatting changes.",
                    suggested_fix="Move working figures outside the print area or delete them.", key=cell.addr + ":font",
                )
                continue
            if cell.col in narrow:
                ctx.emit(
                    "HID-02", sheet=sheet.name, cells=[cell.addr], caption=ctx.caption(sheet, cell.row),
                    explanation="Content sits in a near-zero-width column inside the print area.",
                    suggested_fix="Move the content or hide the column deliberately (after the MULTI-02 safety check).",
                    key=cell.addr + ":narrow",
                )
