"""Hide-not-delete safety (03 §3 preference 4; MULTI-02 / MULTI-03; lesson 15A).

Out-of-scope columns and rows are *hidden*, never deleted, and only when hiding
cannot hide anything else. Hiding a column is sheet-wide, so a column is unsafe
when anything else that prints lives in it (other tables, ageing / maturity
buckets, share counts, signature blocks, DIN lines). A row is unsafe when it
carries printed values that a visible total depends on — unless it is an
intermediate-date balance row of a roll-forward whose visible rows still add
from opening to closing.

Used by the MULTI checks and by the fix planner (hide edits are approval items
and are applied only when the verdict is safe).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from dateutil.relativedelta import relativedelta

from fscore.engine.display import format_info, is_date_format
from fscore.engine.periods import PeriodLabel, find_dates, in_scope, parse_period
from fscore.engine.refs import make_addr
from fscore.engine.statements import header_labels, norm_caption
from fscore.io.xlsx_reader import Sheet

if TYPE_CHECKING:
    from fscore.checks.base import CheckContext
    from fscore.config import EngagementConfig
    from fscore.engine.tables import Table

CURRENCY_RX = re.compile(
    r"(?<![A-Za-z])(USD|AUD|GBP|AED|SGD|EUR|JPY|CAD|CHF|HKD|NZD|ZAR|SAR|MYR|THB|CNY|SEK|NOK|DKK|PHP|IDR|KES|NGN)(?![A-Za-z])"
    r"|functional\s+currency|\bFC\b|(?<![₹\w])[$£€¥](?!\w)",
    re.I,
)
BUCKET_RX = re.compile(
    r"not\s+due|less\s+than\s+\d|more\s+than\s+\d|\d+\s*[-–]\s*\d+\s*(years?|months?|days?)|within\s+\d|"
    r"\d+\s*(months?|years?)\s*[-–]|over\s+\d+\s*(years?|months?)|upto\s+\d|up\s+to\s+\d|\bon\s+demand\b|>\s*\d|<\s*\d",
    re.I,
)
SHARES_RX = re.compile(r"\bshares?\b|number\s+of|\bno\.\s+of\b|\bnos\.?\b|face\s+value", re.I)
BALANCE_ROW_RX = re.compile(
    r"^(balance\s+)?(as\s+at|as\s+on)\b|^opening\b|^closing\b|^balance\s+(at|as\s+at)\b|beginning\s+of|end\s+of\s+the\s+(year|period|quarter)|^gross\s+(carrying|block).*as\s+at",
    re.I,
)
STALE_QUARTER_RX = re.compile(
    r"\b(?:quarter|three\s+months|qtr)\s+(?:ended|ending|to|upto|up\s+to)\s+(?:the\s+)?(?:\d{1,2}(?:st|nd|rd|th)?\s+)?(june|september|sept|december|march)\b",
    re.I,
)
_MONTHS = {"march": 3, "june": 6, "september": 9, "sept": 9, "december": 12}


@dataclass
class HideVerdict:
    safe: bool
    reasons: list[str] = field(default_factory=list)
    blocking_cells: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------- scope


def allowed_row_dates(config: EngagementConfig) -> set[date]:
    """Dates a balance row may carry: period ends and the openings of the 12-month periods."""
    scope = config.scope()
    out = {p.end for p in scope}
    years = [p for p in scope if p.months == 12] or scope
    for p in years:
        start = p.end - relativedelta(months=p.months) + timedelta(days=1)
        out.add(start)
        out.add(start - timedelta(days=1))
    return out


def label_out_of_scope(label: PeriodLabel, config: EngagementConfig) -> bool:
    return label.end is not None and not in_scope(label, config)


def column_currency(ctx: CheckContext, sheet: Sheet, col: int, near_row: int) -> str | None:
    """Currency named in the header rows of a column (None for the presentation currency)."""
    for r in range(max(1, near_row - 6), near_row + 3):
        c = sheet.cell(r, col)
        if c is None or not isinstance(c.value, str):
            continue
        m = CURRENCY_RX.search(c.value)
        if m:
            code = (m.group(1) or m.group(0)).upper()
            if code in ("INR", "₹"):
                return None
            if code.upper() == ctx.config.currency.upper():
                return None
            return code
    return None


def _col_printed(ctx: CheckContext, sheet: Sheet, col: int, header_row: int) -> bool:
    if sheet.col_hidden(col) or sheet.name not in ctx.print_sheet_names:
        return False
    return any(ctx.is_printed(sheet, c.row, c.col) for c in sheet.iter_cells() if c.col == col and c.row >= header_row and c.is_number)


def out_of_scope_columns(ctx: CheckContext) -> list[tuple[str, int, str]]:
    """Printed columns whose header period (or currency) is outside the configured scope."""
    key = "multi:oos_cols"
    if key in ctx.shared:
        return ctx.shared[key]  # type: ignore[no-any-return]
    out: list[tuple[str, int, str]] = []
    seen: set[tuple[str, int]] = set()
    for sh in ctx.print_sheets:
        for r, c, lab in sorted(header_labels(ctx, sh)):
            if (sh.name, c) in seen:
                continue
            fc = column_currency(ctx, sh, c, r) if ctx.config.currency.upper() == "INR" else None
            if not (label_out_of_scope(lab, ctx.config) or fc):
                continue
            if not ctx.is_printed(sh, r, c) or not _col_printed(ctx, sh, c, r):
                continue
            seen.add((sh.name, c))
            out.append((sh.name, c, lab.text if not fc else f"{lab.text} ({fc})"))
    ctx.shared[key] = out
    return out


def _row_out_of_scope_label(ctx: CheckContext, caption: str) -> str | None:
    if not caption or len(caption) > 160:
        return None
    nc = norm_caption(caption)
    allowed = allowed_row_dates(ctx.config)
    ends = sorted(p.end for p in ctx.config.scope())
    for d, _bad, txt in find_dates(caption):
        if d in allowed:
            continue
        if BALANCE_ROW_RX.search(nc) or re.search(r"\b(quarter|period|year|months?)\s+(ended|ending|to)\b", nc):
            return txt
        if ends and d > ends[-1]:
            return txt
    m = STALE_QUARTER_RX.search(caption)
    if m and not find_dates(caption) and _MONTHS[m.group(1).lower()] != ctx.config.year_end.month:
        return m.group(0)
    return None


def out_of_scope_rows(ctx: CheckContext) -> list[tuple[str, int, str]]:
    """Printed rows labelled with an intermediate / out-of-scope date (MULTI-03 candidates)."""
    key = "multi:oos_rows"
    if key in ctx.shared:
        return ctx.shared[key]  # type: ignore[no-any-return]
    out: list[tuple[str, int, str]] = []
    for sh in ctx.print_sheets:
        rows_with_numbers = {c.row for c in ctx.iter_printed_numbers(sh)}
        for r in sorted(rows_with_numbers):
            if sh.row_hidden(r):
                continue
            first_num = min((c.col for c in sh.iter_cells() if c.row == r and c.is_number), default=None)
            cap = ctx.caption(sh, r, before_col=first_num)
            lab = _row_out_of_scope_label(ctx, cap)
            if lab:
                out.append((sh.name, r, cap))
    ctx.shared[key] = out
    return out


# ---------------------------------------------------------------------------- columns


def _inherited_labels(ctx: CheckContext, sh: Sheet) -> dict[tuple[int, int], PeriodLabel | None]:
    """Period of every (table, column), read from the table's own header rows.

    A table without header text in a column inherits the period of the table
    above it in that column (a Balance Sheet is one header and several blocks);
    header text that is not a period (ageing buckets, class names) resets the
    column, so tables further down do not inherit a stale period across it.
    """
    key = f"hide:tlabels:{sh.name}"
    if key in ctx.shared:
        return ctx.shared[key]  # type: ignore[no-any-return]
    labels = header_labels(ctx, sh)
    current: dict[int, PeriodLabel | None] = {}
    out: dict[tuple[int, int], PeriodLabel | None] = {}
    for t in sorted(ctx.tables(sh), key=lambda x: x.r1):
        for col in t.num_cols:
            head = " ".join((t.headers.get(col) or "").split())
            if head:
                lab = parse_period(head)
                current[col] = lab if lab is not None and lab.end is not None else None
            else:
                near = [(r, lab) for r, c, lab in labels if c == col and t.r1 - 6 <= r < t.r1]
                if near:
                    current[col] = max(near, key=lambda x: x[0])[1]
            out[(t.r1, col)] = current.get(col)
    ctx.shared[key] = out
    return out


def table_col_label(ctx: CheckContext, sh: Sheet, t: Table, col: int) -> PeriodLabel | None:
    """Period of a table column (own header, or inherited from the block above)."""
    return _inherited_labels(ctx, sh).get((t.r1, col))


def _hideable_rows_for_column(ctx: CheckContext, sh: Sheet, col: int) -> set[int]:
    rows: set[int] = set()
    for r, c, lab in header_labels(ctx, sh):
        if c == col and (label_out_of_scope(lab, ctx.config) or column_currency(ctx, sh, col, r)):
            rows.add(r)
    for t in ctx.tables(sh):
        if col not in t.num_cols:
            continue
        tlab = table_col_label(ctx, sh, t, col)
        top = min(t.header_rows) if t.header_rows else t.r1
        fc = column_currency(ctx, sh, col, top) if t.header_rows else None
        if (tlab is not None and label_out_of_scope(tlab, ctx.config)) or fc:
            rows.update(range(top, t.r2 + 1))
    return rows


def column_hide_safety(ctx: CheckContext, sheet_name: str, col: int) -> HideVerdict:
    """Can column ``col`` of ``sheet_name`` be hidden without hiding anything else that prints?"""
    from fscore.checks.identity import signature_cells

    sh = ctx.wb.get_sheet(sheet_name)
    if sh is None:
        return HideVerdict(False, ["sheet not found"], [])
    hideable = _hideable_rows_for_column(ctx, sh, col)
    sig = signature_cells(ctx, sh)
    reasons: set[str] = set()
    blocking: list[str] = []
    for cell in sh.iter_cells():
        if cell.col != col or cell.value is None or (isinstance(cell.value, str) and not cell.value.strip()):
            continue
        if cell.row in hideable or not ctx.is_printed(sh, cell.row, cell.col):
            continue
        if format_info(sh.number_format(cell)).hidden:
            continue
        text = cell.value if isinstance(cell.value, str) else ""
        cap = ctx.caption(sh, cell.row)
        header = ctx.column_header(sh, col, cell.row)
        if (cell.row, cell.col) in sig or re.search(r"\bDIN\b|membership|for\s+and\s+on\s+behalf|director|partner", text, re.I):
            reasons.add("signature block")
        elif BUCKET_RX.search(text) or BUCKET_RX.search(header):
            reasons.add("ageing / maturity buckets")
        elif cell.is_number and (SHARES_RX.search(cap) or (format_info(sh.number_format(cell)).decimals == 0 and abs(float(cell.value)) >= 1000)):
            reasons.add("share counts or other 0-decimal figures")
        elif cell.is_number:
            reasons.add("another table's figures")
        else:
            reasons.add("other printed text")
        blocking.append(f"{sh.name}!{make_addr(cell.row, cell.col)}")
    for m in sh.merges:
        if m.c1 == col and m.c2 > col and m.r1 not in hideable:
            anchor = sh.cell(m.r1, m.c1)
            if anchor is not None and anchor.value not in (None, "") and ctx.is_printed(sh, m.r1, m.c1):
                reasons.add("merged heading anchored in the column")
                ref = f"{sh.name}!{make_addr(m.r1, m.c1)}"
                if ref not in blocking:
                    blocking.append(ref)
    # values in the hideable part that visible cross-totals in other columns add up
    low = sh.name.lower()
    for tsheet, trow, tcol, terms in printed_totals(ctx):
        if tsheet == sh.name and tcol == col:
            continue
        for ts, r1, c1, r2, c2 in terms:
            if ts != low or not (c1 <= col <= c2):
                continue
            hit = any(r in hideable and not sh.row_hidden(r) and ctx.displayed_or_zero(sh, r, col) != 0 for r in range(r1, r2 + 1))
            if hit:
                reasons.add("feeds a visible total in another column")
                ref = f"{tsheet}!{make_addr(trow, tcol)}"
                if ref not in blocking:
                    blocking.append(ref)
                break
    return HideVerdict(safe=not blocking, reasons=sorted(reasons), blocking_cells=blocking)


# ---------------------------------------------------------------------------- rows


def _is_balance_row(ctx: CheckContext, sh: Sheet, row: int) -> bool:
    return bool(BALANCE_ROW_RX.search(norm_caption(ctx.caption(sh, row))))


def _row_in_scope_balance(ctx: CheckContext, sh: Sheet, row: int) -> bool:
    cap = ctx.caption(sh, row)
    if not BALANCE_ROW_RX.search(norm_caption(cap)):
        return False
    dates = find_dates(cap)
    if not dates:
        return True  # "Opening balance" / "Closing balance"
    allowed = allowed_row_dates(ctx.config)
    return all(d in allowed for d, _b, _t in dates)


def rollforward_still_adds(ctx: CheckContext, sheet_name: str, row: int) -> bool | None:
    """For a row inside a roll-forward: do the visible rows still add from opening to closing
    if ``row`` is hidden? None when the row is not inside a recognisable roll-forward."""
    sh = ctx.wb.get_sheet(sheet_name)
    if sh is None:
        return None
    table = next((t for t in ctx.tables(sh) if t.r1 <= row <= t.r2), None)
    if table is None:
        return None
    opening = next((r for r in range(row - 1, table.r1 - 1, -1) if _row_in_scope_balance(ctx, sh, r) and not sh.row_hidden(r)), None)
    closing = next((r for r in range(row + 1, table.r2 + 1) if _row_in_scope_balance(ctx, sh, r) and not sh.row_hidden(r)), None)
    if opening is None or closing is None:
        return None
    for col in table.num_cols:
        if col == table.note_col:
            continue
        o = ctx.displayed(sh, opening, col)
        c = ctx.displayed(sh, closing, col)
        if o is None and c is None:
            continue
        total = o or Decimal(0)
        for r in range(opening + 1, closing):
            if r == row or sh.row_hidden(r) or _is_balance_row(ctx, sh, r):
                continue
            v = ctx.displayed(sh, r, col)
            if v is not None and ctx.is_printed(sh, r, col):
                total += v
        if total != (c or Decimal(0)):
            return False
    return True


def printed_totals(ctx: CheckContext) -> list[tuple[str, int, int, list[tuple[str, int, int, int, int]]]]:
    """Index of printed linear totals: (sheet, row, col, [(term sheet, r1, c1, r2, c2)])."""
    key = "hide:totals"
    if key in ctx.shared:
        return ctx.shared[key]  # type: ignore[no-any-return]
    out: list[tuple[str, int, int, list[tuple[str, int, int, int, int]]]] = []
    for other in ctx.print_sheets:
        for cell in other.iter_cells():
            if cell.formula is None or not ctx.is_printed(other, cell.row, cell.col):
                continue
            lf = ctx.linear(other, cell)
            if lf is None or not lf.is_total:
                continue
            out.append((other.name, cell.row, cell.col, [(t.sheet.lower(), t.area.r1, t.area.c1, t.area.r2, t.area.c2) for t in lf.terms]))
    ctx.shared[key] = out
    return out


def visible_dependents(ctx: CheckContext, sh: Sheet, row: int) -> list[str]:
    """Printed totals (any print sheet) that add a non-zero printed value of ``row``."""
    nonzero_cols = [c.col for c in sh.iter_cells() if c.row == row and c.is_number and ctx.displayed_or_zero(sh, row, c.col) != 0]
    if not nonzero_cols:
        return []
    low = sh.name.lower()
    out: list[str] = []
    for tsheet, trow, tcol, terms in printed_totals(ctx):
        if tsheet == sh.name and trow == row:
            continue
        for ts, r1, c1, r2, c2 in terms:
            if ts == low and r1 <= row <= r2 and any(c1 <= c <= c2 for c in nonzero_cols):
                out.append(f"{tsheet}!{make_addr(trow, tcol)}")
                break
    return out


def row_hide_safety(ctx: CheckContext, sheet_name: str, row: int) -> HideVerdict:
    """Can ``row`` be hidden without changing what the visible figures add up to?"""
    from fscore.checks.identity import signature_cells

    sh = ctx.wb.get_sheet(sheet_name)
    if sh is None:
        return HideVerdict(False, ["sheet not found"], [])
    reasons: list[str] = []
    blocking: list[str] = []
    sig = signature_cells(ctx, sh)
    for cell in sh.iter_cells():
        if cell.row == row and (row, cell.col) in sig and ctx.is_printed(sh, row, cell.col):
            reasons.append("signature block on the row")
            blocking.append(f"{sh.name}!{make_addr(row, cell.col)}")
    deps = visible_dependents(ctx, sh, row)
    if deps:
        still = rollforward_still_adds(ctx, sheet_name, row)
        if still is True and _is_balance_row(ctx, sh, row):
            pass  # intermediate balance row: the visible movements still add up
        else:
            reasons.append("holds values that visible totals add" if still is None else "visible rows would no longer add from opening to closing")
            blocking.extend(deps[:10])
    elif any(c.row == row and c.is_number and not is_date_format(sh.number_format(c)) and ctx.displayed_or_zero(sh, row, c.col) != 0
             for c in sh.iter_cells()):
        still = rollforward_still_adds(ctx, sheet_name, row)
        if still is False:
            reasons.append("visible rows would no longer add from opening to closing")
            blocking.append(f"{sh.name}!{make_addr(row, 1)}")
    return HideVerdict(safe=not blocking, reasons=reasons, blocking_cells=blocking)
