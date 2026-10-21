"""TXT-07 — number-format consistency within printed tables (02 §2.1, §6.6).

Per table and column: mixed decimals (0-dp figures beside 2-dp figures),
plain number formats without thousands separators (``0.00`` beside
``#,##0.00``), zero shown as ``0.00`` instead of "-" in accounting-format
tables, mixed negative styles (brackets vs minus) and ``General`` on amounts
(General-format integers such as note numbers are left alone).
One finding per table column (all offending cells), plus one per column whose
decimals differ from the rest of its table.
"""

from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal

from fscore.checks.base import CheckContext
from fscore.engine.display import FormatInfo, format_info
from fscore.engine.refs import index_to_col, make_addr
from fscore.engine.tables import Table
from fscore.io.xlsx_reader import Cell, Sheet
from fscore.registry import check

_COUNT_CAPTION = re.compile(
    r"\bshares?\b|number\s+of|\bno\.\s*of\b|\bnos\b|\bcount\b|%|\bratio\b|\btimes\b|\bdays\b|face\s+value|per\s+share|"
    r"\beps\b|earnings\s+per|\brate\b|\bpercentage\b|\bemployees\b|\bheadcount\b",
    re.I,
)
_MAX_CELLS = 20


def _amount_cells(ctx: CheckContext, sh: Sheet, t: Table, col: int) -> list[Cell]:
    out: list[Cell] = []
    first = min(t.num_cols) if t.num_cols else None
    for row in t.rows:
        cell = sh.cell(row, col)
        if cell is None or not cell.is_number or not ctx.is_printed(sh, row, col):
            continue
        info = format_info(sh.number_format(cell))
        if info.kind in ("percent", "date", "scientific") or info.hidden:
            continue
        if _COUNT_CAPTION.search(ctx.caption(sh, row, before_col=first)):
            continue
        out.append(cell)
    return out


def _column_issues(ctx: CheckContext, sh: Sheet, cells: list[Cell]) -> tuple[list[str], list[Cell], int | None]:
    """(issue texts, offending cells, majority decimals) for one table column."""
    infos: list[tuple[Cell, FormatInfo]] = [(c, format_info(sh.number_format(c))) for c in cells]
    issues: list[str] = []
    bad: dict[tuple[int, int], Cell] = {}
    general = [c for c, i in infos if i.kind == "general" and not float(c.value).is_integer()]
    if general:
        issues.append(f"{len(general)} amount(s) in the General format (print unrounded)")
        bad.update({(c.row, c.col): c for c in general})
    fmt = [(c, i) for c, i in infos if i.kind != "general"]
    if not fmt:
        return issues, list(bad.values()), None
    decs = Counter(i.decimals for _c, i in fmt)
    major = decs.most_common(1)[0][0]
    if len(fmt) >= 2:
        odd = [c for c, i in fmt if i.decimals != major]
        if odd:
            others = ", ".join(str(d) for d in sorted(decs) if d != major)
            issues.append(f"{len(odd)} figure(s) with {others} decimal(s) beside {major}-decimal figures")
            bad.update({(c.row, c.col): c for c in odd})
        grp = Counter(i.grouping for _c, i in fmt)
        if grp[True] > grp[False] and grp[False]:
            plain = [c for c, i in fmt if not i.grouping]
            issues.append(f"{len(plain)} figure(s) in a plain number format without thousands separators")
            bad.update({(c.row, c.col): c for c in plain})
        dash = Counter(i.zero_dash for _c, i in fmt)
        if dash[True] > dash[False]:
            zeros = [c for c, _i in fmt if ctx.displayed(sh, c.row, c.col) == 0 and ctx.printed_text(sh, c.row, c.col) not in ("-", "–", "")]
            if zeros:
                issues.append(f"{len(zeros)} zero figure(s) printed as '{ctx.printed_text(sh, zeros[0].row, zeros[0].col)}' instead of '-'")
                bad.update({(c.row, c.col): c for c in zeros})
        negs = [c for c, _i in fmt if (ctx.displayed(sh, c.row, c.col) or Decimal(0)) < 0]
        styles = Counter("brackets" if "(" in ctx.printed_text(sh, c.row, c.col) else "minus" for c in negs)
        if len(styles) > 1:
            minority = min(styles, key=lambda k: styles[k])
            odd_neg = [c for c in negs if ("(" in ctx.printed_text(sh, c.row, c.col)) == (minority == "brackets")]
            issues.append("negative figures shown both in brackets and with a minus sign")
            bad.update({(c.row, c.col): c for c in odd_neg})
    return issues, sorted(bad.values(), key=lambda c: (c.row, c.col)), major


def _global_decimals(ctx: CheckContext) -> int | None:
    """Most common decimals of printed amounts across all print sheets (the house convention)."""
    key = "txt07:global"
    if key not in ctx.shared:
        cnt: Counter[int] = Counter()
        for sh in ctx.print_sheets:
            for t in ctx.tables(sh):
                for col in t.num_cols:
                    if col == t.note_col or _COUNT_CAPTION.search(t.headers.get(col, "") or ""):
                        continue
                    for c in _amount_cells(ctx, sh, t, col):
                        info = format_info(sh.number_format(c))
                        if info.kind == "number":
                            cnt[info.decimals] += 1
        ctx.shared[key] = cnt.most_common(1)[0][0] if cnt else None
    return ctx.shared[key]  # type: ignore[no-any-return]


@check("TXT-07")
def check_formats(ctx: CheckContext) -> None:
    house = _global_decimals(ctx)
    for sh in ctx.print_sheets:
        for t in ctx.tables(sh):
            cols = [c for c in t.num_cols if c != t.note_col]
            for col in cols:
                if _COUNT_CAPTION.search(t.headers.get(col, "") or ""):
                    continue  # share counts, percentages, rates …
                cells = _amount_cells(ctx, sh, t, col)
                if not cells:
                    continue
                issues, bad, major = _column_issues(ctx, sh, cells)
                letter = index_to_col(col)
                where = f"'{t.title}'" if t.title else f"the table at row {t.r1}"
                if issues:
                    ctx.emit(
                        "TXT-07", sheet=sh.name, cells=[make_addr(c.row, c.col) for c in bad[:_MAX_CELLS]],
                        explanation=f"Column {letter} of {where} has inconsistent number formats: {'; '.join(issues)}.",
                        printed=ctx.printed_text(sh, bad[0].row, bad[0].col) if bad else None,
                        suggested_fix="Apply the table's accounting format (2 decimals, thousands separators, zero as '-', negatives in brackets).",
                        key=f"col:{t.r1}:{col}",
                    )
                elif house is not None and major is not None and major != house and len(cells) >= 2:
                    ctx.emit(
                        "TXT-07", sheet=sh.name, cells=[make_addr(cells[0].row, col)],
                        explanation=f"Column {letter} of {where} shows {major}-decimal figures; the statements use {house} decimals.",
                        suggested_fix=f"Use {house} decimals, as elsewhere in the statements.", key=f"coldec:{t.r1}:{col}",
                    )
