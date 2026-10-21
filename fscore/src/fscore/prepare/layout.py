"""Row/column helpers for laying out statements and notes in house style.

Formulas may contain reference tokens ``«sheet|row-id|period»`` so a page can
point at cells of pages that are laid out later (e.g. the EPS note uses the
P&L profit, the P&L uses the EPS note). ``resolve_tokens`` turns them into
A1 references once every page exists.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from fscore.engine.refs import index_to_col, make_addr, quote_sheet
from fscore.prepare.spec import CellSpec, SheetSpec, WorkbookSpec

TOKEN_RE = re.compile(r"«([^|»]+)\|([^|»]+)\|([^»|]+)»")


class Fx(str):
    """Marks a value as a formula (text without '=')."""

    __slots__ = ()


def tok(sheet: str, rid: str, period: str) -> str:
    return f"«{sheet}|{rid}|{period}»"


@dataclass(frozen=True)
class ColSpec:
    key: str  # period key ("CY", "PY", "Q4_CY", …) or a column role ("shares", "pct", "total", …)
    col: int
    header: str = ""
    style: str = "amount"


class Page:
    """A print page (statement or note sheet) built top-down."""

    def __init__(
        self,
        spec: WorkbookSpec,
        name: str,
        role: str,
        cols: Sequence[ColSpec],
        *,
        code: str,
        note_col: int | None = None,
        caption_width: float = 52.0,
        amount_width: float = 14.5,
        landscape: bool = False,
        selected: bool = True,
    ) -> None:
        self.spec = spec
        self.code = code
        self.sheet: SheetSpec = spec.add(SheetSpec(name=name, role=role, selected=selected, landscape=landscape))
        self.cols: dict[str, ColSpec] = {c.key: c for c in cols}
        self.note_col = note_col
        self.r = 1
        self.sheet.col_widths[1] = caption_width
        if note_col:
            self.sheet.col_widths[note_col] = 6.5
        for c in cols:
            self.sheet.col_widths.setdefault(c.col, amount_width)
        self.max_col = max([c.col for c in cols] + [note_col or 1, 1])

    # ------------------------------------------------------------------ addressing
    @property
    def name(self) -> str:
        return self.sheet.name

    def set_cols(self, cols: Sequence[ColSpec]) -> None:
        """Switch the active column set (a note sheet can hold tables of different shapes)."""
        self.cols = {c.key: c for c in cols}
        for c in cols:
            self.sheet.col_widths.setdefault(c.col, 14.5)
        self.max_col = max([self.max_col, *[c.col for c in cols]])

    def row(self, rid: str) -> int:
        return self.sheet.ids[rid]

    def col_of(self, rid: str, key: str) -> int:
        return page_col(self, key, rid)

    def addr(self, rid: str, key: str) -> str:
        return make_addr(self.row(rid), self.col_of(rid, key))

    def ref(self, rid: str, key: str, col: int | None = None) -> str:
        """Token referring to a cell of this page (resolved later)."""
        return tok(self.name, rid, key if col is None else f"#{col}")

    def index(self, rid: str, alias: str | None = None, cols: dict[str, int] | None = None) -> None:
        idx = self.spec.entity.setdefault("index", {})
        entry = (self.name, self.row(rid), cols or dict(self.sheet.meta.get("row_cols", {}).get(rid) or {k: c.col for k, c in self.cols.items()}))
        idx[f"{self.code}.{rid}"] = entry
        if alias:
            idx[alias] = entry

    # ------------------------------------------------------------------ cells
    def put(self, row: int, col: int, value: Any, style: str, lineage: str = "") -> CellSpec | None:
        if value is None:
            return self.sheet.set(row, col, None, style=style)
        if isinstance(value, Fx):
            return self.sheet.set(row, col, formula=str(value), style=style, lineage=lineage)
        if isinstance(value, Decimal):
            return self.sheet.set(row, col, float(value), style=style, lineage=lineage)
        return self.sheet.set(row, col, value, style=style, lineage=lineage)

    def _rid(self, rid: str | None) -> None:
        if rid:
            if rid in self.sheet.ids:
                raise ValueError(f"duplicate row id {rid!r} on {self.name}")
            self.sheet.ids[rid] = self.r
            self.sheet.meta.setdefault("row_cols", {})[rid] = {k: c.col for k, c in self.cols.items()}

    def blank(self, n: int = 1) -> None:
        self.r += n

    def title(self, entity: str, title: str, unit_line: str) -> None:
        self.put(self.r, 1, entity, "title")
        self.r += 1
        self.put(self.r, 1, title, "subtitle")
        self.r += 1
        self.put(self.r, 1, unit_line, "unit")
        self.r += 2

    def header(self, first: str = "Particulars", *, note_label: str = "Note", rid: str | None = None, keys: Iterable[str] | None = None) -> None:
        self._rid(rid)
        self.put(self.r, 1, first, "header_left")
        if self.note_col:
            self.put(self.r, self.note_col, note_label, "header_center")
        for k, c in self.cols.items():
            if keys is not None and k not in keys:
                continue
            self.put(self.r, c.col, c.header, "header")
        self.sheet.row_heights[self.r] = 26
        self.r += 1

    def section(self, text: str, *, style: str = "section", rid: str | None = None, note: str | None = None) -> int:
        self._rid(rid)
        self.put(self.r, 1, text, style)
        if note and self.note_col:
            self.put(self.r, self.note_col, note, "note_ref")
        self.r += 1
        return self.r - 1

    def line(
        self,
        rid: str | None,
        caption: str,
        values: dict[str, Any],
        *,
        note: str | int | None = None,
        cap: str = "caption",
        style: str | None = None,
        lineage: str = "",
        hidden: bool = False,
    ) -> int:
        self._rid(rid)
        self.put(self.r, 1, caption, cap)
        if note is not None and self.note_col:
            self.put(self.r, self.note_col, str(note) if not isinstance(note, int) else note, "note_ref")
        for k, v in values.items():
            c = self.cols[k]
            self.put(self.r, c.col, v, style or c.style, lineage)
        if hidden:
            self.sheet.hidden_rows.add(self.r)
        self.r += 1
        return self.r - 1

    def total(
        self,
        rid: str | None,
        caption: str,
        members: Sequence[str],
        *,
        keys: Iterable[str] | None = None,
        cap: str = "caption_bold",
        style: str = "amount_bold",
        contiguous: bool = True,
        signs: dict[str, int] | None = None,
    ) -> int:
        """A total of member rows: SUM over a contiguous block, else explicit +/− terms."""
        rows = [self.row(m) for m in members]
        self._rid(rid)
        self.put(self.r, 1, caption, cap)
        for k in keys or self.cols:
            c = self.cols[k]
            letter = index_to_col(c.col)
            if (contiguous and not signs and len(rows) > 1 and sorted(rows) == list(range(min(rows), max(rows) + 1))) or (contiguous and not signs and len(rows) > 1 and all(r2 - r1 <= 3 for r1, r2 in zip(sorted(rows), sorted(rows)[1:], strict=False))):
                f = f"SUM({letter}{min(rows)}:{letter}{max(rows)})"
            else:
                parts = []
                for m, rr in zip(members, rows, strict=True):
                    sgn = (signs or {}).get(m, 1)
                    parts.append(("-" if sgn < 0 else "+") + f"{letter}{rr}")
                f = "".join(parts).lstrip("+")
            self.put(self.r, c.col, Fx(f), style)
        self.r += 1
        return self.r - 1

    def text(self, text: str, *, style: str = "narrative", rid: str | None = None, height: float | None = None, merge: bool = True, hidden: bool = False) -> int:
        self._rid(rid)
        self.put(self.r, 1, text, style)
        if merge and self.max_col > 1:
            self.sheet.merges.append((self.r, 1, self.r, self.max_col))
        # Excel and LibreOffice do not auto-fit rows of wrapped (and merged) text: size the row from the
        # characters that fit across the cells actually used (Arial 9 pt ≈ 1.1 characters per width unit)
        last = self.max_col if merge and self.max_col > 1 else 1
        width = sum(self.sheet.col_widths.get(c, 8.43) for c in range(1, last + 1))
        per_line = max(20, int(width * 1.1))
        lines = sum(max(1, math.ceil(len(part) / per_line)) for part in (text or " ").split("\n"))
        self.sheet.row_heights[self.r] = height or 11.6 * lines + 3
        if hidden:
            self.sheet.hidden_rows.add(self.r)
        self.r += 1
        return self.r - 1

    def finish(self, *, last_col: int | None = None) -> None:
        self.sheet.print_area = (1, 1, max(self.r - 1, 1), last_col or self.max_col)


# ---------------------------------------------------------------------- tokens


def resolve_tokens(spec: WorkbookSpec, pages: dict[str, Page]) -> None:
    """Replace «sheet|rid|period» tokens in every formula with A1 references."""
    by_name = {p.name: p for p in pages.values()}

    def repl_for(own_sheet: str) -> Any:
        def repl(m: re.Match[str]) -> str:
            sheet, rid, key = m.group(1), m.group(2), m.group(3)
            page = by_name.get(sheet)
            if page is None:
                raise KeyError(f"token refers to unknown sheet {sheet!r}")
            row = page.sheet.ids.get(rid)
            if row is None:
                raise KeyError(f"token refers to unknown row id {rid!r} on {sheet!r}")
            col = int(key[1:]) if key.startswith("#") else page_col(page, key, rid)
            a = make_addr(row, col)
            return a if sheet == own_sheet else f"{quote_sheet(sheet)}!{a}"

        return repl

    for s in spec.sheets:
        rp = repl_for(s.name)
        for cs in s.cells.values():
            if cs.formula and "«" in cs.formula:
                cs.formula = TOKEN_RE.sub(rp, cs.formula)


def page_col(page: Page, key: str, rid: str) -> int:
    cols = page.sheet.meta.get("row_cols", {}).get(rid)
    if cols and key in cols:
        return int(cols[key])
    if key in page.cols:
        return page.cols[key].col
    raise KeyError(f"column {key!r} unknown on {page.name} (row {rid})")


# ---------------------------------------------------------------------- rounding helpers


def q2(x: Decimal, places: int = 2) -> Decimal:
    return x.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def round_to_total(values: Sequence[Decimal], total: Decimal, places: int = 2) -> list[Decimal]:
    """Round values so their sum equals the rounded total (largest-remainder method).

    Used for typed tables (actuarial movements, prior-year cash flow) so the
    printed components add up to the printed total, as a preparer would do.
    """
    unit = Decimal(1).scaleb(-places)
    floors = [(v / unit).to_integral_value(rounding="ROUND_FLOOR") * unit for v in values]
    target = q2(total, places)
    diff = int(((target - sum(floors, Decimal(0))) / unit).to_integral_value())
    order = sorted(range(len(values)), key=lambda i: values[i] - floors[i], reverse=True)
    out = list(floors)
    if diff >= 0:
        for i in order[:diff]:
            out[i] += unit
    else:
        for i in list(reversed(order))[: -diff]:
            out[i] -= unit
    return out
