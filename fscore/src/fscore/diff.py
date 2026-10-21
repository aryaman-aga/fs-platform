"""Version diff between two workbooks (06 §5, control C12).

Reports per-sheet cell changes (formula, value, number format), "printed
figures moved" (displayed text of printed cells before → after, with PY and
key figures flagged), hidden rows / columns / sheet states and print-area
changes, and sheets added or removed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fscore.config import EngagementConfig
from fscore.engine.display import is_date_format
from fscore.engine.evaluator import values_equal
from fscore.engine.printsel import select_print_sheets
from fscore.engine.refs import index_to_col, make_addr
from fscore.io.xlsx_reader import Cell, Sheet, Workbook
from fscore.models import CellChange, PrintedMove, WorkbookDiff

KEY_CAPTION_RE = re.compile(
    r"profit\s*/?\s*\(?loss\)?\s+before\s+tax|\(?loss\)?\s*/?\s*profit\s+before\s+tax|profit\s+before\s+tax"
    r"|(profit|loss|\(loss\)|profit\s*/\s*\(loss\))\s+(after\s+tax|for\s+the\s+(year|period|quarter))"
    r"|total\s+comprehensive\s+(income|loss)"
    r"|earnings\s+per\s+(equity\s+)?share|\beps\b|\bbasic\b|\bdiluted\b"
    r"|total\s+assets|total\s+equity(?!\s+and)|net\s+worth"
    r"|cash\s+and\s+cash\s+equivalents",
    re.I,
)
_MONTHS = "jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
_YEAR_PATTERNS = [
    re.compile(rf"(?:{_MONTHS})[\s.,'-]*\d{{1,2}}(?:st|nd|rd|th)?[\s.,'-]+(\d{{4}})", re.I),  # March 31, 2026
    re.compile(rf"\d{{1,2}}(?:st|nd|rd|th)?[\s.-]+(?:{_MONTHS})[\s.,'-]+(\d{{4}})", re.I),  # 31 March 2026
    re.compile(r"\d{1,2}[./-]\d{1,2}[./-](\d{4})"),  # 31.03.2026
    re.compile(r"(?:fy|f\.y\.)\s*'?\d{2,4}\s*[-–/]\s*'?(\d{2,4})", re.I),  # FY 25-26
    re.compile(r"\b(?:19|20)\d{2}\s*[-–/]\s*(\d{2,4})\b"),  # 2025-26
    re.compile(rf"(?:{_MONTHS})[\s'-]*(\d{{2}})\b(?!\d)", re.I),  # Mar-26, Qtr Mar 26
    re.compile(r"\b((?:19|20)\d{2})\b"),  # any year
]
_QUARTER_RE = re.compile(r"quarter|qtr|three\s+months|3\s+months", re.I)


def header_end_year(text: str) -> int | None:
    """Year in which the period of a column header ends (``None`` if no year is recognisable)."""
    for rx in _YEAR_PATTERNS:
        m = rx.search(text)
        if m:
            y = int(m.group(1))
            if y < 100:
                y += 2000
            return y
    return None


def classify_period(header: str, config: EngagementConfig) -> tuple[str | None, bool]:
    """(period label, is_py) for a column header, relative to the configured year end."""
    if not header:
        return None, False
    year = header_end_year(header)
    if year is None:
        return None, False
    cy = config.year_end.year
    prefix = "Q4 " if _QUARTER_RE.search(header) else ""
    if year == cy:
        return prefix + "CY", False
    if year == cy - 1:
        return prefix + "PY", True
    return str(year), year < cy


def _content(c: Cell | None) -> bool:
    return c is not None and (c.value is not None or c.formula is not None)


def _repr_value(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float):
        return format(v, ".15g")
    return str(v)


def _repr_cell(c: Cell | None) -> str:
    if c is None:
        return ""
    if c.formula is not None:
        return "=" + c.formula
    return _repr_value(c.value)


def _caption(sheet: Sheet, row: int, before_col: int) -> str:
    parts: list[str] = []
    for col in range(1, before_col):
        c = sheet.cell(row, col)
        if c is not None and isinstance(c.value, str) and c.value.strip():
            parts.append(c.value.strip())
    return " ".join(parts)


def _header_above(sheet: Sheet, row: int, col: int, max_up: int = 40) -> str:
    for r in range(row - 1, max(0, row - max_up), -1):
        c = sheet.cell(r, col)
        if c is None or c.value is None:
            continue
        if isinstance(c.value, str) and c.value.strip():
            return c.value.strip()
        if is_date_format(sheet.number_format(c)):
            return sheet.wb.render_cell(c).clean
    return ""


def _printed(sheet: Sheet, row: int, col: int, print_names: set[str]) -> bool:
    return sheet.name in print_names and sheet.visible and sheet.is_printed(row, col)


def _printed_text(sheet: Sheet, c: Cell | None) -> str:
    if c is None or c.value is None:
        return ""
    return sheet.wb.render_cell(c).clean


def diff(a: Path, b: Path, config: EngagementConfig) -> WorkbookDiff:
    """Compare two workbook versions ``a`` (before) and ``b`` (after)."""
    with Workbook.open(a) as wa, Workbook.open(b) as wb:
        out = WorkbookDiff(a_sha256=wa.sha256, b_sha256=wb.sha256)
        names_a = [s.name for s in wa.sheets]
        names_b = [s.name for s in wb.sheets]
        out.sheets_added = [n for n in names_b if n not in names_a]
        out.sheets_removed = [n for n in names_a if n not in names_b]
        print_a = {s.name for s in select_print_sheets(wa, config)}
        print_b = {s.name for s in select_print_sheets(wb, config)}
        for name in [n for n in names_a if n in names_b]:
            sa, sb = wa.sheet(name), wb.sheet(name)
            _diff_sheet(sa, sb, out, print_a, print_b, config)
        return out


def _diff_sheet(sa: Sheet, sb: Sheet, out: WorkbookDiff, print_a: set[str], print_b: set[str], config: EngagementConfig) -> None:
    name = sa.name
    # ---------------------------------------------------------------- visibility and layout
    if sa.state != sb.state:
        out.hidden_changes.append(f"{name}: sheet {sa.state} → {sb.state}")
    for r in sorted(sb.rows_hidden - sa.rows_hidden):
        out.hidden_changes.append(f"{name}: row {r} hidden")
    for r in sorted(sa.rows_hidden - sb.rows_hidden):
        out.hidden_changes.append(f"{name}: row {r} unhidden")
    for c in sorted(sb.cols_hidden - sa.cols_hidden):
        out.hidden_changes.append(f"{name}: column {index_to_col(c)} hidden")
    for c in sorted(sa.cols_hidden - sb.cols_hidden):
        out.hidden_changes.append(f"{name}: column {index_to_col(c)} unhidden")
    pa = [x.a1() for x in sa.print_area or []]
    pb = [x.a1() for x in sb.print_area or []]
    if pa != pb:
        out.hidden_changes.append(f"{name}: print area {','.join(pa) or '(none)'} → {','.join(pb) or '(none)'}")
    # ---------------------------------------------------------------- cells
    keys = sorted(set(sa.cells) | set(sb.cells))
    for key in keys:
        ca, cb = sa.cells.get(key), sb.cells.get(key)
        addr = make_addr(*key)
        has_a, has_b = _content(ca), _content(cb)
        if has_a and not has_b:
            out.changes.append(CellChange(sheet=name, cell=addr, kind="removed", before=_repr_cell(ca), after=""))
        elif has_b and not has_a:
            out.changes.append(CellChange(sheet=name, cell=addr, kind="added", before="", after=_repr_cell(cb)))
        elif ca is not None and cb is not None:
            if (ca.formula or None) != (cb.formula or None):
                out.changes.append(CellChange(sheet=name, cell=addr, kind="formula", before=_repr_cell(ca), after=_repr_cell(cb)))
            elif not values_equal(ca.value, cb.value):
                out.changes.append(CellChange(sheet=name, cell=addr, kind="value", before=_repr_value(ca.value), after=_repr_value(cb.value)))
        fa = sa.number_format(ca) if ca is not None else "General"
        fb = sb.number_format(cb) if cb is not None else "General"
        if (has_a or has_b) and fa != fb:
            out.changes.append(CellChange(sheet=name, cell=addr, kind="style", before=fa, after=fb))
        # ------------------------------------------------------------ printed figures moved
        pr_a = has_a and _printed(sa, key[0], key[1], print_a)
        pr_b = has_b and _printed(sb, key[0], key[1], print_b)
        if not (pr_a or pr_b):
            continue
        before = _printed_text(sa, ca) if pr_a else ""
        after = _printed_text(sb, cb) if pr_b else ""
        if before == after:
            continue
        ref_sheet = sb if pr_b else sa
        caption = _caption(ref_sheet, key[0], key[1])
        period, is_py = classify_period(_header_above(ref_sheet, key[0], key[1]), config)
        out.printed_moves.append(
            PrintedMove(
                sheet=name, cell=addr, caption=caption or None, before=before, after=after,
                period=period, is_py=is_py, is_key=bool(caption and KEY_CAPTION_RE.search(caption)),
            )
        )
