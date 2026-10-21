"""Evaluator self-test (02 §4): re-evaluate every print-sheet formula against
Excel's cached values of its precedents and compare with Excel's cached result.
At least 99.5 % must match before any fix is attempted.
"""

from __future__ import annotations

from typing import Any

from fscore.engine import formula as F
from fscore.engine.evaluator import Evaluator, Unsupported, values_equal
from fscore.engine.refs import make_addr
from fscore.io.xlsx_reader import Workbook
from fscore.models import SelfTestReport

THRESHOLD = 0.995


def workbook_getter(wb: Workbook) -> Any:
    def get(sheet: str, row: int, col: int) -> Any:
        s = wb.get_sheet(sheet)
        if s is None:
            from fscore.engine.evaluator import REF

            return REF
        c = s.cell(row, col)
        return None if c is None else c.value

    return get


def name_lookup(wb: Workbook) -> Any:
    def lookup(name: str, sheet: str) -> str | None:
        s = wb.get_sheet(sheet)
        idx = s.index if s is not None else None
        return wb.name_value(name, idx)

    return lookup


def bounds(wb: Workbook) -> Any:
    def b(sheet: str) -> tuple[int, int]:
        s = wb.get_sheet(sheet)
        if s is None:
            return (1, 1)
        return (max(s.max_row, 1), max(s.max_col, 1))

    return b


def run_selftest(wb: Workbook, sheet_names: list[str], *, max_mismatches: int = 50) -> SelfTestReport:
    ev = Evaluator(workbook_getter(wb), name_lookup=name_lookup(wb), bounds=bounds(wb), date1904=wb.date1904)
    rep = SelfTestReport()
    unsupported: set[str] = set()
    for name in sheet_names:
        sh = wb.get_sheet(name)
        if sh is None:
            continue
        for c in sh.iter_cells():
            if c.formula is None:
                continue
            rep.formulas += 1
            if c.value is None:
                rep.missing_cache += 1
                continue
            try:
                v = ev.evaluate(c.formula, sh.name, c.row, c.col)
            except Unsupported as exc:
                rep.unsupported += 1
                unsupported.add(exc.what)
                continue
            except (F.FormulaError, RecursionError, ZeroDivisionError, OverflowError, ValueError, TypeError):
                rep.unsupported += 1
                unsupported.add("parse")
                continue
            rep.evaluated += 1
            if values_equal(c.value, v):
                rep.matched += 1
            elif len(rep.mismatches) < max_mismatches:
                rep.mismatches.append(f"{sh.name}!{make_addr(c.row, c.col)}")
    rep.unsupported_functions = sorted(unsupported)
    return rep
