"""Independent test oracle for synthetic workbooks.

A deliberately simple re-implementation of "does every printed total add up
on paper?" and the balance-sheet / cash-flow ties, used to derive the
expected findings of exact-mode fixtures and to prove the clean fixtures are
clean. It reads the written .xlsx back, so it tests the file, not the spec.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from fscore.engine import formula as F
from fscore.engine.display import format_info, is_date_format
from fscore.engine.linear import linear_form
from fscore.engine.refs import make_addr
from fscore.io.xlsx_reader import Workbook

D = Decimal


@dataclass(frozen=True)
class CastIssue:
    sheet: str
    cell: str
    printed: Decimal
    components: Decimal
    difference: Decimal
    formula: str


def _printed(wb: Workbook, sheet: str, row: int, col: int) -> Decimal | None:
    s = wb.get_sheet(sheet)
    if s is None:
        return None
    c = s.cell(row, col)
    if c is None or c.value is None or isinstance(c.value, (str, bool)):
        return None
    if is_date_format(s.number_format(c)):
        return None
    return wb.render_cell(c).value


def print_sheets(wb: Workbook) -> list[str]:
    grouped = [s.name for s in wb.sheets if s.visible and s.tab_selected]
    return grouped or [s.name for s in wb.sheets if s.visible]


def scan_casting(path: str | Path, sheets: list[str] | None = None) -> list[CastIssue]:
    """Every visible linear total in the print area vs the sum of its printed components."""
    out: list[CastIssue] = []
    with Workbook.open(path) as wb:
        names = sheets or print_sheets(wb)
        for name in names:
            s = wb.sheet(name)
            for c in s.iter_cells():
                if not c.formula or not c.is_number:
                    continue
                if not s.is_printed(c.row, c.col) or format_info(s.number_format(c)).hidden:
                    continue
                try:
                    lf = linear_form(F.parse(c.formula), s.name)
                except F.FormulaError:
                    continue
                if lf is None or not lf.is_total:
                    continue
                total = D(0)
                for t in lf.terms:
                    for r, col in t.area.cells():
                        v = _printed(wb, t.sheet, r, col)
                        if v is not None:
                            total += t.coef * v
                printed = wb.render_cell(c).value
                if printed is None:
                    continue
                if printed != total:
                    out.append(CastIssue(s.name, c.addr, printed, total, printed - total, c.formula))
    return out


def tie_gaps(path: str | Path, index: dict[str, Any], periods: tuple[str, ...] = ("CY", "PY")) -> dict[str, dict[str, Decimal]]:
    """Printed BS gap (total assets − total equity and liabilities) and CF gap (CF closing − BS cash)."""
    gaps: dict[str, dict[str, Decimal]] = {"bs": {}, "cf": {}}
    with Workbook.open(path) as wb:
        def val(alias: str, k: str) -> Decimal:
            sheet, row, cols = index[alias]
            return _printed(wb, sheet, row, cols[k]) or D(0)

        for k in periods:
            gaps["bs"][k] = val("bs.total_assets", k) - val("bs.total_eq_liab", k)
            gaps["cf"][k] = val("cf.closing_cash", k) - val("bs.cash", k)
    return gaps


def cell_addr(row: int, col: int) -> str:
    return make_addr(row, col)


def simulate_totals_fix(path: str | Path, index: dict[str, Any], periods: tuple[str, ...] = ("CY", "PY")) -> dict[str, dict[str, Decimal]]:
    """BS / CF gaps left after every printed total is rewritten to add its printed components.

    This is the state the balancing solver starts from (02 §7.2: totals-only
    rewrite to a fixed point, then the balancing lines). Formula cells are
    recalculated; constants keep their cached values.
    """
    from fscore.engine.calc import Recalculator

    with Workbook.open(path) as wb:
        names = set(print_sheets(wb))
        formulas: dict[tuple[str, int, int], str] = {}
        for s in wb.sheets:
            for c in s.iter_cells():
                if c.formula is None:
                    continue
                f = c.formula
                if s.name in names and c.is_number and s.is_printed(c.row, c.col):
                    try:
                        lf = linear_form(F.parse(f), s.name)
                    except F.FormulaError:
                        lf = None
                    if lf is not None and lf.is_total:
                        parts = []
                        for t in lf.terms:
                            d = 2
                            for r, col in t.area.cells():
                                cc = wb.cell(t.sheet, r, col)
                                if cc is not None and cc.is_number:
                                    d = format_info(wb.sheet(t.sheet).number_format(cc)).value_decimals
                                    break
                            sign = "-" if t.coef < 0 else "+"
                            body = f"ROUND({t.text},{d})" if t.area.is_cell else f"SUMPRODUCT(ROUND({t.text},{d}))"
                            parts.append(sign + body)
                        f = "".join(parts).lstrip("+") or "0"
                formulas[(s.name, c.row, c.col)] = f

        def base(k: tuple[str, int, int]) -> Any:
            c = wb.cell(*k)
            return None if c is None else c.value

        rc = Recalculator(formulas, base, bounds=lambda sh: (wb.sheet(sh).max_row or 1, wb.sheet(sh).max_col or 1))
        vals = rc.compute_all()

        def printed(alias: str, k: str) -> Decimal:
            sheet, row, cols = index[alias]
            col = cols[k]
            v = vals.get((sheet, row, col))
            if v is None:
                v = base((sheet, row, col))
            cell = wb.cell(sheet, row, col)
            fmt = wb.sheet(sheet).number_format(cell) if cell is not None else "0.00"
            from fscore.engine.display import render

            out = render(v, fmt).value
            return out if out is not None else D(0)

        gaps: dict[str, dict[str, Decimal]] = {"bs": {}, "cf": {}}
        for k in periods:
            gaps["bs"][k] = printed("bs.total_assets", k) - printed("bs.total_eq_liab", k)
            gaps["cf"][k] = printed("cf.closing_cash", k) - printed("bs.cash", k)
        return gaps
