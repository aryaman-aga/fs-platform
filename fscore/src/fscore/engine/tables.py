"""Table detection on print sheets (02 §5 tables.py).

A table is a block of rows holding printed numbers, with header rows above
(text in the numeric columns), a caption column (row labels), period columns,
"Total" columns and total rows. Detection is purely content-based.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from fscore.engine.statements import norm_caption, period_columns_at
from fscore.io.xlsx_reader import Sheet

if TYPE_CHECKING:
    from fscore.checks.base import CheckContext

_TOTAL_HDR = re.compile(r"^\s*(grand\s+)?total(\s+.*)?$", re.I)
_NOTE_HDR = re.compile(r"^\s*notes?(\s+no\.?)?\s*$", re.I)
_ITEM_HEADING = re.compile(r"^\s*\(?([a-z]|[ivx]{1,4})\)\s+\S", re.I)


@dataclass
class Table:
    sheet: str
    r1: int
    r2: int
    caption_col: int
    num_cols: list[int]
    header_rows: list[int] = field(default_factory=list)
    headers: dict[int, str] = field(default_factory=dict)
    period_cols: dict[str, int] = field(default_factory=dict)
    total_cols: list[int] = field(default_factory=list)
    total_rows: list[int] = field(default_factory=list)
    rows: list[int] = field(default_factory=list)  # rows with printed numbers
    title: str = ""
    note_col: int | None = None

    def caption(self, ctx: CheckContext, row: int) -> str:
        sh = ctx.wb.sheet(self.sheet)
        return ctx.caption(sh, row, before_col=min(self.num_cols) if self.num_cols else None)

    def component_cols(self, total_col: int) -> list[int]:
        """Numeric columns that a 'Total' column adds across (left of it, excluding notes/other totals)."""
        return [c for c in self.num_cols if c < total_col and c not in self.total_cols and c != self.note_col]


def _is_header_row(ctx: CheckContext, sh: Sheet, row: int, num_cols: set[int]) -> bool:
    texts = 0
    nums = 0
    for col in num_cols:
        c = sh.cell(row, col)
        if c is None or c.value is None:
            continue
        if isinstance(c.value, str) and c.value.strip():
            texts += 1
        elif c.is_number:
            from fscore.engine.display import is_date_format

            if is_date_format(sh.number_format(c)):
                texts += 1
            else:
                nums += 1
    return texts >= 1 and nums == 0


def detect_tables(ctx: CheckContext, sh: Sheet) -> list[Table]:
    nums_by_row: dict[int, set[int]] = {}
    for c in ctx.iter_printed_numbers(sh):
        nums_by_row.setdefault(c.row, set()).add(c.col)
    if not nums_by_row:
        return []
    all_num_cols: Counter[int] = Counter()
    for cols in nums_by_row.values():
        all_num_cols.update(cols)
    # note-reference columns hold small integers only (statement faces)
    note_cols: set[int] = set()
    for col in all_num_cols:
        for r in range(1, min(sh.max_row, 40) + 1):
            hc = sh.cell(r, col)
            if hc is not None and isinstance(hc.value, str) and _NOTE_HDR.match(hc.value):
                note_cols.add(col)
                break
    rows_sorted = sorted(nums_by_row)
    blocks: list[list[int]] = []
    cur: list[int] = []
    for r in rows_sorted:
        if cur:
            gap_rows = range(cur[-1] + 1, r)
            header_between = any(_is_header_row(ctx, sh, g, set(all_num_cols)) for g in gap_rows)
            # an item-labelled heading with no figures ("(b) Disclosure under the MSMED Act") starts a new table
            item_heading = any(_ITEM_HEADING.match(ctx.caption(sh, g) or "") for g in gap_rows)
            if header_between or item_heading or (r - cur[-1] > 3):
                blocks.append(cur)
                cur = []
        cur.append(r)
    if cur:
        blocks.append(cur)
    tables: list[Table] = []
    for blk in blocks:
        num_cols_counter: Counter[int] = Counter()
        for r in blk:
            num_cols_counter.update(nums_by_row[r] - note_cols)
        num_cols = sorted(num_cols_counter)
        if not num_cols:
            continue
        first_num = min(num_cols)
        cap_counter: Counter[int] = Counter()
        for r in blk:
            for col in range(1, first_num):
                lc = sh.cell(r, col)
                if lc is not None and isinstance(lc.value, str) and lc.value.strip():
                    cap_counter[col] += 1
                    break
        caption_col = cap_counter.most_common(1)[0][0] if cap_counter else max(1, first_num - 1)
        t = Table(sh.name, blk[0], blk[-1], caption_col, num_cols, rows=list(blk))
        t.note_col = next((c for c in note_cols if c < first_num or c in num_cols), None)
        # header rows directly above (up to 5 rows, contiguous header-ish rows)
        hdr_rows: list[int] = []
        r = blk[0] - 1
        while r >= 1 and blk[0] - r <= 6:
            if _is_header_row(ctx, sh, r, set(num_cols)):
                hdr_rows.insert(0, r)
            elif hdr_rows:
                break
            r -= 1
        t.header_rows = hdr_rows
        for col in num_cols:
            parts = []
            for hr in hdr_rows:
                hcell = sh.cell(hr, col)
                if hcell is None or hcell.value is None:
                    anchor = sh.merged_anchor(hr, col)
                    if anchor is not None:
                        hcell = sh.cell(*anchor)
                if hcell is not None and hcell.value is not None:
                    parts.append(ctx.wb.render_cell(hcell).clean if not isinstance(hcell.value, str) else hcell.value.strip())
            t.headers[col] = " ".join(p for p in parts if p)
        t.total_cols = [c for c in num_cols if _TOTAL_HDR.match(t.headers.get(c, "") or "") and len(num_cols) >= 3]
        t.period_cols = period_columns_at(ctx, sh, blk[0])
        # title: nearest text above the header block in the caption column (or col A)
        top = (hdr_rows[0] if hdr_rows else blk[0]) - 1
        for rr in range(top, max(0, top - 6), -1):
            txt = ctx.caption(sh, rr)
            if txt:
                t.title = txt
                break
        # total rows: caption says total, or every number in the row is a linear total of cells above
        for r in blk:
            cap = norm_caption(ctx.caption(sh, r, before_col=first_num))
            if re.search(r"^(sub[\s-]*)?total\b|\btotal$|^net\s+(block|carrying)|^closing|^balance\s+as\s+at\s+(the\s+)?end", cap):
                t.total_rows.append(r)
                continue
            formula_totals = 0
            cells = 0
            for col in nums_by_row[r]:
                cell = sh.cell(r, col)
                if cell is None:
                    continue
                cells += 1
                lf = ctx.linear(sh, cell) if cell.formula else None
                if lf is not None and lf.is_total and all(term.sheet == sh.name and term.area.r2 < r for term in lf.terms):
                    formula_totals += 1
            if cells and formula_totals == cells and not cap.startswith(("add", "less")):
                t.total_rows.append(r)
        tables.append(t)
    return tables
