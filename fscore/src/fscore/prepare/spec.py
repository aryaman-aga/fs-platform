"""In-memory specification of a workbook to be written (Module P and the synthetic generator).

A ``WorkbookSpec`` is plain data: sheets of ``CellSpec`` (constant or formula
plus a house-style key), print setup, hidden rows/columns. It is evaluated
by :mod:`fscore.engine.calc` and written with cached values by
:mod:`fscore.prepare.writer`, so the file opens with correct figures before
Excel recalculates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fscore.engine.refs import make_addr, quote_sheet


@dataclass
class CellSpec:
    value: Any = None
    formula: str | None = None  # without '='
    style: str = "text"
    lineage: str = ""  # e.g. "tb:tr.good" — the TB mapping key(s) behind a printed figure


@dataclass
class SheetSpec:
    name: str
    role: str = "note"  # bs, pl, cf, soce, note, rpt, gc, ratios, policies, tb, backup
    cells: dict[tuple[int, int], CellSpec] = field(default_factory=dict)
    col_widths: dict[int, float] = field(default_factory=dict)
    row_heights: dict[int, float] = field(default_factory=dict)
    hidden_rows: set[int] = field(default_factory=set)
    hidden_cols: set[int] = field(default_factory=set)
    print_area: tuple[int, int, int, int] | None = None  # r1, c1, r2, c2
    landscape: bool = False
    fit_width: bool = True
    header: str = ""
    footer: str = ""
    selected: bool = False
    state: str = "visible"
    merges: list[tuple[int, int, int, int]] = field(default_factory=list)
    ids: dict[str, int] = field(default_factory=dict)  # logical row id -> row number
    meta: dict[str, Any] = field(default_factory=dict)
    next_row: int = 1

    def set(self, row: int, col: int, value: Any = None, *, formula: str | None = None, style: str = "text", lineage: str = "") -> CellSpec:
        if formula is not None and formula.startswith("="):
            formula = formula[1:]
        c = CellSpec(value=value, formula=formula, style=style, lineage=lineage)
        self.cells[(row, col)] = c
        return c

    def get(self, row: int, col: int) -> CellSpec | None:
        return self.cells.get((row, col))

    def ref(self, row: int, col: int, *, from_sheet: str | None = None, absolute: bool = False) -> str:
        a = make_addr(row, col)
        if absolute:
            from fscore.engine.refs import index_to_col

            a = f"${index_to_col(col)}${row}"
        if from_sheet == self.name:
            return a
        return f"{quote_sheet(self.name)}!{a}"

    def row_of(self, rid: str) -> int:
        return self.ids[rid]

    @property
    def max_row(self) -> int:
        return max((r for r, _c in self.cells), default=0)

    @property
    def max_col(self) -> int:
        return max((c for _r, c in self.cells), default=0)


@dataclass
class WorkbookSpec:
    sheets: list[SheetSpec] = field(default_factory=list)
    entity: dict[str, Any] = field(default_factory=dict)
    templates: dict[str, str] = field(default_factory=dict)  # template id -> version (E-RULE-02)

    def add(self, sheet: SheetSpec) -> SheetSpec:
        self.sheets.append(sheet)
        return sheet

    def sheet(self, name: str) -> SheetSpec:
        for s in self.sheets:
            if s.name == name:
                return s
        raise KeyError(name)

    def by_role(self, role: str) -> list[SheetSpec]:
        return [s for s in self.sheets if s.role == role]

    def formulas(self) -> dict[tuple[str, int, int], str]:
        return {(s.name, r, c): cs.formula for s in self.sheets for (r, c), cs in s.cells.items() if cs.formula is not None}

    def constant(self, key: tuple[str, int, int]) -> Any:
        try:
            cs = self.sheet(key[0]).cells.get((key[1], key[2]))
        except KeyError:
            return None
        return None if cs is None or cs.formula is not None else cs.value
