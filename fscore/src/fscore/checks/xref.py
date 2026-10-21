"""Cross-reference family (02 §6.3): XREF-01 link equality, XREF-02 duplicate-figure
discovery (+ the user-confirmed cross-reference map), XREF-04 previous-year
consistency. XREF-03 mandatory tie-outs live in ``tie_outs.py``.

This module also holds the small helpers the other cross-checking modules
(tie-outs, roll-forward, schedules, share capital, ratios) share.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from rapidfuzz import fuzz

from fscore.checks.base import CheckContext, fmt_amount, is_total_caption
from fscore.engine.display import format_info, is_date_format, parse_printed_number
from fscore.engine.periods import parse_period, scope_key
from fscore.engine.refs import make_addr, split_addr
from fscore.engine.statements import NoteLoc, norm_caption, period_columns_at
from fscore.io.xlsx_reader import Sheet
from fscore.models import CellRef, Evidence, XrefPair
from fscore.registry import check

if TYPE_CHECKING:
    from fscore.engine.tables import Table

ZERO = Decimal(0)
ROUNDING_TWIN = Decimal("0.05")


# =============================================================================== shared helpers


@dataclass(frozen=True)
class Loc:
    """A printed figure: sheet, row, column and its displayed value."""

    sheet: str
    row: int
    col: int
    value: Decimal | None

    @property
    def addr(self) -> str:
        return make_addr(self.row, self.col)

    @property
    def ref(self) -> CellRef:
        return CellRef(sheet=self.sheet, addr=self.addr)


def loc(ctx: CheckContext, sheet: Sheet | str, row: int, col: int) -> Loc:
    sh = sheet if isinstance(sheet, Sheet) else ctx.wb.sheet(sheet)
    return Loc(sh.name, row, col, ctx.displayed(sh, row, col))


def has_number(ctx: CheckContext, sh: Sheet, row: int, col: int) -> bool:
    c = sh.cell(row, col)
    return c is not None and c.is_number and not is_date_format(sh.number_format(c))


def ev(ctx: CheckContext, x: Loc, label: str | None = None) -> Evidence:
    return ctx.evidence_for(x.sheet, x.row, x.col, label)


def amt(d: Decimal | None) -> str:
    return fmt_amount(d) if d is not None else "(blank)"


def mismatch_status(a: Decimal, b: Decimal) -> str:
    d = abs(a - b)
    if d == 0:
        return "ok"
    return "rounding_mismatch" if d <= ROUNDING_TWIN else "mismatch"


def line_loc(ctx: CheckContext, key: str, period: str) -> Loc | None:
    """Displayed value of a key statement line (``ctx.statements.lines``) for one period."""
    ln = ctx.statements.line(key)
    if ln is None or period not in ln.cols:
        return None
    sh = ctx.wb.sheet(ln.sheet)
    col = ln.cols[period]
    c = sh.cell(ln.row, col)
    if c is None or c.value is None:
        return Loc(sh.name, ln.row, col, None)
    return Loc(sh.name, ln.row, col, ctx.displayed(sh, ln.row, col))


def line_periods(ctx: CheckContext, key: str) -> list[str]:
    ln = ctx.statements.line(key)
    return list(ln.cols) if ln is not None else []


def caption_rows(ctx: CheckContext, sh: Sheet, rx: re.Pattern[str], r1: int = 1, r2: int | None = None) -> list[int]:
    """Rows whose normalised caption matches ``rx`` (search)."""
    out: list[int] = []
    for row in range(max(1, r1), (r2 or sh.max_row) + 1):
        cap = ctx.caption(sh, row)
        if cap and rx.search(norm_caption(cap)):
            out.append(row)
    return out


def row_numbers(ctx: CheckContext, sh: Sheet, row: int) -> list[int]:
    """Columns of the printed numbers on a row (left to right)."""
    out = []
    for col in range(1, sh.max_col + 1):
        if has_number(ctx, sh, row, col) and ctx.is_printed(sh, row, col):
            out.append(col)
    return out


def section_of(ctx: CheckContext, sh: Sheet, row: int, r_min: int = 1) -> str | None:
    """'non_current' / 'current' from the nearest heading above (e.g. 'Non-current assets')."""
    for r in range(row - 1, max(r_min, 1) - 1, -1):
        cap = norm_caption(ctx.caption(sh, r))
        if not cap:
            continue
        if re.search(r"\bnon[\s-]*current\b", cap):
            return "non_current"
        if re.search(r"^(total\s+)?current\b|\bcurrent\s+(assets|liabilities|portion|maturities)\b|^current$", cap) and not cap.startswith("total"):
            return "current"
        if cap.startswith("total") and re.search(r"current", cap):
            return None
    return None


def find_note(ctx: CheckContext, title_rx: re.Pattern[str]) -> NoteLoc | None:
    for n in sorted(ctx.statements.notes.values(), key=lambda n: (n.sheet, n.row)):
        if title_rx.search(n.title.lower()):
            return n
    return None


def note_by_number(ctx: CheckContext, num: str) -> NoteLoc | None:
    return ctx.statements.notes.get(num)


def note_tables(ctx: CheckContext, note: NoteLoc) -> list[Table]:
    sh = ctx.wb.sheet(note.sheet)
    return [t for t in ctx.tables(sh) if note.row <= t.r1 <= (note.end_row or sh.max_row)]


def table_sections(ctx: CheckContext, sh: Sheet, title_rx: re.Pattern[str]) -> list[Table]:
    """Tables on any print sheet whose title (or note heading above) matches."""
    out = []
    for s in ctx.print_sheets:
        for t in ctx.tables(s):
            if title_rx.search((t.title or "").lower()):
                out.append(t)
    return out


def all_tables(ctx: CheckContext) -> list[tuple[Sheet, Table]]:
    out = []
    for s in ctx.print_sheets:
        for t in ctx.tables(s):
            out.append((s, t))
    return out


def heading_above(ctx: CheckContext, sh: Sheet, row: int, max_up: int = 12) -> str:
    """Nearest text-only row above ``row`` (a sub-heading), normalised."""
    for r in range(row - 1, max(0, row - max_up), -1):
        cap = ctx.caption(sh, r)
        if cap and not row_numbers(ctx, sh, r):
            return norm_caption(cap)
    return ""


def table_at(ctx: CheckContext, sh: Sheet, row: int) -> Table | None:
    """The detected table whose header-to-last-row span contains ``row``."""
    for t in ctx.tables(sh):
        top = t.header_rows[0] if t.header_rows else t.r1
        if top <= row <= t.r2:
            return t
    return None


def col_period(ctx: CheckContext, sh: Sheet, row: int, col: int) -> str | None:
    """Scope key of a column at ``row`` from the table's *own* header row.

    A table with header rows whose header for ``col`` is not a period (e.g.
    "Computers", "Number of shares", "% change") has no period column there —
    never fall back to a header of an earlier table further up the sheet.
    """
    t = table_at(ctx, sh, row)
    if t is not None and t.header_rows:
        lab = parse_period(t.headers.get(col, "") or "")
        if lab is not None and lab.end is not None:
            return scope_key(lab, ctx.config)
        if _subtable_break(ctx, sh, t.header_rows[-1], row):
            return _note_period_header(ctx, sh, row, col)  # e.g. an "(b) …" disclosure under the note's own CY / PY header
        return None
    for k, c in period_columns_at(ctx, sh, row).items():
        if c == col:
            return k
    return None


_SUBTABLE = re.compile(r"^\s*\(?([a-z]|[ivx]{1,4})\)\s+\S", re.I)


def _subtable_break(ctx: CheckContext, sh: Sheet, header_row: int, row: int) -> bool:
    """An item-labelled heading ("(b) Dues to micro and small enterprises") between a table header and ``row``."""
    for r in range(header_row + 1, row):
        if row_numbers(ctx, sh, r):
            continue
        if _SUBTABLE.match(ctx.caption(sh, r) or ""):
            return True
    return False


def _note_period_header(ctx: CheckContext, sh: Sheet, row: int, col: int) -> str | None:
    """Nearest period header above ``row`` in ``col`` that belongs to the same note."""
    from fscore.engine.statements import header_labels

    start = max(1, row - 60)
    for n in ctx.statements.notes.values():
        if n.sheet == sh.name and n.row <= row <= (n.end_row or sh.max_row):
            start = max(start, n.row)
    best: tuple[int, str | None] | None = None
    for r, c, lab in header_labels(ctx, sh):
        if c == col and start <= r < row and (best is None or r > best[0]):
            best = (r, scope_key(lab, ctx.config))
    return best[1] if best else None


def row_heading_period(ctx: CheckContext, sh: Sheet, row: int) -> str | None:
    """Period from a short heading row above (e.g. "As at March 31, 2025" splitting one table into blocks)."""
    t = table_at(ctx, sh, row)
    top = (t.header_rows[-1] + 1) if t is not None and t.header_rows else max(1, row - 12)
    for r in range(row - 1, top - 1, -1):
        if row_numbers(ctx, sh, r):
            continue
        cap = ctx.caption(sh, r)
        if cap and len(cap) < 80:
            lab = parse_period(cap)
            if lab is not None and lab.end is not None:
                return scope_key(lab, ctx.config)
    return None


def cell_period(ctx: CheckContext, sh: Sheet, row: int, col: int) -> str | None:
    """Scope key of a printed number: table column header, dated row caption, or block heading."""
    k = col_period(ctx, sh, row, col)
    if k is not None:
        return k
    t = table_at(ctx, sh, row)
    if t is None or not t.header_rows or parse_period(t.headers.get(col, "") or "") is None:
        cap = ctx.caption(sh, row)
        lab = parse_period(cap) if re.search(r"\bas\s+(at|on)\b|^balance|^at\s", norm_caption(cap)) else None
        if lab is not None and lab.end is not None:
            return scope_key(lab, ctx.config)
        return row_heading_period(ctx, sh, row)
    return None


def periods_at(ctx: CheckContext, sh: Sheet, row: int) -> dict[str, int]:
    """Scope key -> column at ``row`` (table-header aware replacement of period_columns_at)."""
    t = table_at(ctx, sh, row)
    if t is not None and t.header_rows:
        out: dict[str, int] = {}
        for c in sorted(t.num_cols):
            k = col_period(ctx, sh, row, c)
            if k is not None and k not in out:
                out[k] = c
        return out
    return period_columns_at(ctx, sh, row)


def period_of_cell(ctx: CheckContext, sh: Sheet, row: int, col: int) -> str | None:
    return cell_period(ctx, sh, row, col)


def linear_components(ctx: CheckContext, sh: Sheet, row: int, col: int) -> set[tuple[str, int, int]] | None:
    """Cells a linear-total formula adds (each range expanded), or None if not a linear total."""
    c = sh.cell(row, col)
    if c is None or c.formula is None:
        return None
    lf = ctx.linear(sh, c)
    if lf is None or not lf.terms or not lf.unit:
        return None
    out: set[tuple[str, int, int]] = set()
    for t in lf.terms:
        target = ctx.wb.get_sheet(t.sheet)
        name = target.name if target is not None else t.sheet
        for r, cc in t.area.cells():
            out.add((name, r, cc))
    return out


def is_linear_of(ctx: CheckContext, x: Loc, parts: list[Loc]) -> bool:
    """True when ``x`` is a linear formula whose non-empty components are exactly ``parts``."""
    comps = linear_components(ctx, ctx.wb.sheet(x.sheet), x.row, x.col)
    if comps is None:
        return False
    want = {(p.sheet, p.row, p.col) for p in parts}
    have = set()
    for s, r, c in comps:
        sh = ctx.wb.get_sheet(s)
        cell = sh.cell(r, c) if sh is not None else None
        if cell is not None and cell.value not in (None, "", 0, 0.0):
            have.add((s, r, c))
    want_nonzero = {w for w in want if (ctx.wb.sheet(w[0]).cell(w[1], w[2]) is not None and ctx.wb.sheet(w[0]).cell(w[1], w[2]).value not in (None, "", 0, 0.0))}  # type: ignore[union-attr]
    return have == want_nonzero


def link_target(ctx: CheckContext, sh: Sheet, row: int, col: int) -> tuple[str, int, int, int] | None:
    """(sheet, row, col, sign) when the cell is a pure link (=X or =-X, optional ROUND)."""
    c = sh.cell(row, col)
    if c is None or c.formula is None:
        return None
    lf = ctx.linear(sh, c)
    if lf is None or len(lf.terms) != 1 or lf.constant != 0 or lf.has_sum:
        return None
    t = lf.terms[0]
    if not t.is_cell or t.coef not in (Decimal(1), Decimal(-1)):
        return None
    target = ctx.wb.get_sheet(t.sheet)
    if target is None:
        return None
    return (target.name, t.area.r1, t.area.c1, int(t.coef))


# ------------------------------------------------------------------ rounding-path awareness

HALF_PAISA = Decimal("0.005")


def unrounded(ctx: CheckContext, sheet: str | Sheet, row: int, col: int, *, _depth: int = 0) -> Decimal | None:
    """The figure behind a printed cell, without rounding and without approved balancing adjustments.

    Linear formulas (SUM, +/-, ROUND/SUMPRODUCT(ROUND()) wrappers, scaled links) are
    re-added from their components' raw values, so two cells that show the same
    economic figure through different rounding paths (e.g. after the totals-only
    fix) compare equal; the '+adj' constant of a configured balancing line is
    ignored (02 §2.4, control C3).
    """
    sh = sheet if isinstance(sheet, Sheet) else ctx.wb.get_sheet(sheet)
    if sh is None:
        return None
    key = f"unr:{sh.name}:{row}:{col}"
    if key in ctx.shared:
        return ctx.shared[key]  # type: ignore[no-any-return]
    cell = sh.cell(row, col)
    out: Decimal | None
    if cell is None or cell.value is None:
        out = ZERO
    elif not cell.is_number:
        out = None
    else:
        out = Decimal(repr(float(cell.value)))
        lf = ctx.linear(sh, cell) if cell.formula and _depth < 8 else None
        if lf is None and cell.formula:
            adj = _balancing_constant(ctx, sh, cell)
            if adj:
                out -= adj  # e.g. =SUMIF(…)/100000+0.03 on the configured balancing line
        if lf is not None and lf.terms and sum(t.area.size for t in lf.terms) <= 400:
            total = ZERO
            ok = True
            for t in lf.terms:
                tsh = ctx.wb.get_sheet(t.sheet)
                if tsh is None:
                    ok = False
                    break
                for r, c in t.area.cells():
                    v = unrounded(ctx, tsh, r, c, _depth=_depth + 1)
                    total += t.coef * (v if v is not None else ZERO)
            if ok:
                from fscore.checks.casting import is_balancing_cell

                const = ZERO if is_balancing_cell(ctx, sh, cell) else lf.constant
                out = total + const
    ctx.shared[key] = out
    return out


def _balancing_constant(ctx: CheckContext, sh: Sheet, cell: object) -> Decimal:
    """The visible '+adj' of a configured balancing line whose base formula is not linear (SUMIF, …)."""
    from fscore.checks.casting import is_balancing_cell
    from fscore.engine import formula as F

    if not is_balancing_cell(ctx, sh, cell):  # type: ignore[arg-type]
        return ZERO
    node = ctx.parsed(sh, cell)  # type: ignore[arg-type]
    if isinstance(node, F.Binary) and node.op in "+-" and isinstance(node.right, F.Num):
        v = Decimal(node.right.text or repr(node.right.value))
        v = v if node.op == "+" else -v
        if abs(v) <= ctx.config.policy.balancing_threshold:
            return v
    return ZERO


def sums_rounding_only(ctx: CheckContext, a_parts: list[tuple[Loc, int]], b_parts: list[tuple[Loc, int]], *, absolute: bool = False,
                       tolerance: Decimal | None = None) -> bool:
    """Σ sign × a and Σ sign × b differ on paper only through rounding paths or balancing adjustments.

    The printed difference may be up to a few paise plus the balancing threshold
    (an approved '+adj' flows into other figures); the figures behind the print
    must then agree to within half a paisa.
    """
    if tolerance is None:
        tolerance = Decimal("0.05") + ctx.config.policy.balancing_threshold
    if not a_parts or not b_parts or any(p.value is None for p, _s in a_parts + b_parts):
        return False
    av = sum((sg * (p.value or ZERO) for p, sg in a_parts), ZERO)
    bv = sum((sg * (p.value or ZERO) for p, sg in b_parts), ZERO)
    if absolute:
        av, bv = abs(av), abs(bv)
    if av == bv:
        return True
    if abs(av - bv) > tolerance:
        return False
    ua = [unrounded(ctx, p.sheet, p.row, p.col) for p, _s in a_parts]
    ub = [unrounded(ctx, p.sheet, p.row, p.col) for p, _s in b_parts]
    if any(u is None for u in ua + ub):
        return False
    sa = sum((sg * (u or ZERO) for (_p, sg), u in zip(a_parts, ua, strict=True)), ZERO)
    sb = sum((sg * (u or ZERO) for (_p, sg), u in zip(b_parts, ub, strict=True)), ZERO)
    if absolute:
        sa, sb = abs(sa), abs(sb)
    return abs(sa - sb) < HALF_PAISA


def rounding_only(ctx: CheckContext, x: Loc, parts: list[tuple[Loc, int]], *, absolute: bool = False,
                  tolerance: Decimal | None = None) -> bool:
    """``x`` and Σ sign × part print differently only through rounding paths or balancing adjustments."""
    if x.value is None:
        return False
    return sums_rounding_only(ctx, [(x, 1)], parts, absolute=absolute, tolerance=tolerance)


def same_figure(ctx: CheckContext, a: Loc, b: Loc, *, sign: int = 1, absolute: bool = False, tolerance: Decimal | None = None) -> bool:
    """Equal on paper, or a paisa-level difference that only comes from rounding paths / balancing adjustments."""
    if a.value is None or b.value is None:
        return True
    return rounding_only(ctx, a, [(b, sign)], absolute=absolute, tolerance=tolerance)


def add_pair(ctx: CheckContext, a: Loc, b: Loc, *, status: str, source: str, confirmed: bool = False) -> None:
    pairs: list[XrefPair] = ctx.shared.setdefault("xref_map", [])
    key = (a.sheet, a.addr, b.sheet, b.addr)
    seen: set[tuple[str, str, str, str]] = ctx.shared.setdefault("xref_map_keys", set())
    if key in seen or (key[2], key[3], key[0], key[1]) in seen:
        return
    seen.add(key)
    sa, sb = ctx.wb.sheet(a.sheet), ctx.wb.sheet(b.sheet)
    pairs.append(
        XrefPair(
            a=a.ref, b=b.ref,
            caption_a=ctx.caption(sa, a.row), caption_b=ctx.caption(sb, b.row),
            printed_a=ctx.printed_text(sa, a.row, a.col), printed_b=ctx.printed_text(sb, b.row, b.col),
            status=status, source=source, confirmed=confirmed,
        )
    )


def unit_multiplier(ctx: CheckContext) -> int:
    return ctx.config.unit_divisor


# =============================================================================== XREF-01


@check("XREF-01")
def check_links(ctx: CheckContext) -> None:
    """Every direct link between print cells must print the same figure (formats can differ)."""
    for sh in ctx.print_sheets:
        for cell in ctx.iter_printed_numbers(sh):
            tgt = link_target(ctx, sh, cell.row, cell.col)
            if tgt is None:
                continue
            tsheet, tr, tc, sign = tgt
            src = ctx.wb.sheet(tsheet)
            if src.name == sh.name and tr == cell.row and tc == cell.col:
                continue
            if not ctx.is_printed(src, tr, tc):
                continue
            src_cell = src.cell(tr, tc)
            if src_cell is None or not src_cell.is_number:
                continue
            a = loc(ctx, sh, cell.row, cell.col)
            b = loc(ctx, src, tr, tc)
            if a.value is None or b.value is None:
                continue
            expected = b.value * sign
            status = mismatch_status(a.value, expected)
            if src.name != sh.name:
                add_pair(ctx, a, b, status=status, source="link", confirmed=True)
            if status == "ok":
                continue
            fa = format_info(sh.number_format(cell)).code
            fb = format_info(src.number_format(src_cell)).code
            why = "the two cells use different number formats" if fa != fb else "the link is rounded differently"
            ctx.emit(
                "XREF-01",
                sheet=sh.name,
                cells=[a.addr],
                caption=ctx.caption(sh, cell.row),
                printed=ctx.printed_text(sh, cell.row, cell.col),
                expected=ctx.printed_text(src, tr, tc) if sign == 1 else amt(expected),
                difference=a.value - expected,
                explanation=f"{sh.name}!{a.addr} links to {src.name}!{b.addr} but prints {amt(a.value)} against {amt(expected)}: {why}.",
                evidence=[ev(ctx, a, "Link"), ev(ctx, b, "Source")],
                suggested_fix="Use the same number format (decimals) for the link and its source.",
                key=f"{src.name}!{b.addr}",
            )


# =============================================================================== XREF-02

_GENERIC = re.compile(
    r"^(sub[\s-]*)?total$|^(grand\s+)?total$|^others?$|^net$|^gross$|^current$|^non[\s-]*current$|^particulars$|^amount$"
    r"|^balance$|^opening\s+balance$|^closing\s+balance$|^less:?$|^add:?$|^nil$|^na$|^n\.a\.?$",
)
_BALANCE_ROW = re.compile(r"balance|closing|at the end|as at|total|outstanding")
_WEAK_WORDS = {"total", "balance", "year", "period", "note", "notes", "company", "the", "and", "for", "other", "less", "add", "amount",
               "particulars", "statement", "current", "non", "items", "income", "expenses", "loss", "profit"}


@dataclass
class NumEntry:
    sheet: str
    row: int
    col: int
    value: Decimal
    caption: str  # normalised row caption
    subject: str  # header + table / section context (period words removed)
    period: str | None
    role: str | None

    @property
    def key(self) -> tuple[str, int, int]:
        return (self.sheet, self.row, self.col)


_PERIOD_WORDS = re.compile(
    r"\b(as at|as on|year|years|ended|for the|period|quarter|months?|three|six|nine|twelve|march|june|september|december|"
    r"april|31st?|30th?|1st|\d{1,2}|20\d\d|fy|qtr|balance|at the|end of|beginning of)\b"
)


def _subject_words(text: str) -> str:
    t = norm_caption(text)
    t = _PERIOD_WORDS.sub(" ", t)
    t = re.sub(r"[^a-z& ]+", " ", t)
    return " ".join(w for w in t.split() if len(w) > 2)


def build_index(ctx: CheckContext) -> list[NumEntry]:
    if "xref_index" in ctx.shared:
        return ctx.shared["xref_index"]  # type: ignore[no-any-return]
    roles = {v: k for k, v in ctx.statements.roles.items()}
    out: list[NumEntry] = []
    for sh in ctx.print_sheets:
        for cell in ctx.iter_printed_numbers(sh):
            fi = format_info(sh.number_format(cell))
            if fi.kind not in ("number", "general") or fi.percent:
                continue
            v = ctx.displayed(sh, cell.row, cell.col)
            if v is None or v == 0:
                continue
            cap = norm_caption(ctx.caption(sh, cell.row))
            if not cap:
                continue
            hdr = ctx.column_header(sh, cell.col, cell.row)
            sect = heading_above(ctx, sh, cell.row)
            subject = _subject_words(" ".join([hdr if parse_period(hdr) is None else "", sect]))
            out.append(NumEntry(sh.name, cell.row, cell.col, v, cap, subject, period_of_cell(ctx, sh, cell.row, cell.col), roles.get(sh.name)))
    ctx.shared["xref_index"] = out
    return out


def _significant(caption: str) -> bool:
    if _GENERIC.match(caption):
        return False
    core = re.sub(r"^(sub[\s-]*)?total\s*|^less:?\s*|^add:?\s*|^net\s+", "", caption)
    letters = re.sub(r"[^a-z]", "", core)
    if len(letters) < 6:
        return False
    lab = parse_period(caption)
    return not (lab is not None and len(letters) < 25)


def _linked(ctx: CheckContext, a: NumEntry, b: NumEntry) -> bool:
    """Direct link either way, or both links to the same source cell."""
    ta = link_target(ctx, ctx.wb.sheet(a.sheet), a.row, a.col)
    tb = link_target(ctx, ctx.wb.sheet(b.sheet), b.row, b.col)
    if ta is not None and ta[:3] == b.key:
        return True
    if tb is not None and tb[:3] == a.key:
        return True
    return ta is not None and tb is not None and ta[:3] == tb[:3]


def _note_scope(ctx: CheckContext) -> dict[tuple[str, int], set[tuple[str, int, int]]]:
    """Face-statement rows with note refs -> the note block's cells (covered by XREF-03)."""
    out: dict[tuple[str, int], set[tuple[str, int, int]]] = {}
    for num, rows in ctx.statements.note_refs.items():
        note = ctx.statements.notes.get(num)
        if note is None:
            continue
        for sname, row in rows:
            out[(sname, row)] = {(note.sheet, r, 0) for r in range(note.row, (note.end_row or note.row) + 1)}
    return out


def load_xref_map(path: str | Path) -> list[XrefPair]:
    """Load the user-confirmed cross-reference map (xref_map.yaml) for reuse in later years."""
    data: Any = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    out: list[XrefPair] = []
    for p in data.get("pairs", []):
        out.append(
            XrefPair(
                a=CellRef(sheet=p["a"]["sheet"], addr=p["a"]["addr"]),
                b=CellRef(sheet=p["b"]["sheet"], addr=p["b"]["addr"]),
                caption_a=p.get("caption_a", ""), caption_b=p.get("caption_b", ""),
                status=p.get("status", "proposed"), source="map", confirmed=bool(p.get("confirmed", True)),
            )
        )
    return out


def dump_xref_map(pairs: list[XrefPair], *, only_confirmed: bool = False) -> str:
    rows = []
    for p in pairs:
        if only_confirmed and not p.confirmed:
            continue
        rows.append({
            "a": {"sheet": p.a.sheet, "addr": p.a.addr}, "b": {"sheet": p.b.sheet, "addr": p.b.addr},
            "caption_a": p.caption_a, "caption_b": p.caption_b, "status": p.status, "source": p.source, "confirmed": p.confirmed,
        })
    return yaml.safe_dump({"version": 1, "pairs": rows}, sort_keys=False, allow_unicode=True)


def _relocate(ctx: CheckContext, ref: CellRef, caption: str) -> Loc | None:
    """Find a mapped cell again; when rows moved, search the sheet for the same caption."""
    sh = ctx.wb.get_sheet(ref.sheet)
    if sh is None:
        return None
    r, c = split_addr(ref.addr)
    if not caption or fuzz.token_sort_ratio(norm_caption(ctx.caption(sh, r)), norm_caption(caption)) >= 80:
        return loc(ctx, sh, r, c)
    target = norm_caption(caption)
    for row in range(1, sh.max_row + 1):
        if fuzz.token_sort_ratio(norm_caption(ctx.caption(sh, row)), target) >= 95 and has_number(ctx, sh, row, c):
            return loc(ctx, sh, row, c)
    return None


def reserve_pairs(ctx: CheckContext) -> list[tuple[Loc, Loc, str]]:
    """SOCE reserve columns vs the other-equity note closing balances (same figure, two places)."""
    sname = ctx.statements.sheet("soce")
    note = find_note(ctx, re.compile(r"other\s+equity|reserves\s+and\s+surplus"))
    if not sname or note is None:
        return []
    soce = ctx.wb.sheet(sname)
    nsh = ctx.wb.sheet(note.sheet)
    out: list[tuple[Loc, Loc, str]] = []
    # SOCE: header columns -> reserve names; dated rows -> periods
    hdr_cols: dict[int, str] = {}
    for t in ctx.tables(soce):
        for col, h in t.headers.items():
            hn = norm_caption(h)
            if hn and "total" not in hn and "share capital" not in hn:
                hdr_cols[col] = hn
    dated: dict[str, int] = {}
    for row in range(1, soce.max_row + 1):
        lab = parse_period(ctx.caption(soce, row))
        if lab is None or lab.end is None or not re.search(r"balance|as at", ctx.caption(soce, row).lower()):
            continue
        k = scope_key(lab, ctx.config)
        if k in ("CY", "PY") and row_numbers(ctx, soce, row):
            dated[k] = row  # last occurrence of the period end wins (closing balance)
    if not hdr_cols or not dated:
        return []
    # note: section headings (e.g. "Retained earnings") followed by a closing row
    r1, r2 = note.row, note.end_row or nsh.max_row
    sections: list[tuple[int, str]] = []
    for row in range(r1, r2 + 1):
        cap = norm_caption(ctx.caption(nsh, row))
        if cap and not row_numbers(ctx, nsh, row):
            sections.append((row, cap))
    for col, reserve in hdr_cols.items():
        best = None
        for srow, scap in sections:
            score = fuzz.token_set_ratio(scap, reserve)
            if score >= 90 and (best is None or score > best[0]):
                best = (score, srow)
        if best is None:
            continue
        srow = best[1]
        nxt = [r for r, _ in sections if r > srow]
        send = (min(nxt) - 1) if nxt else r2
        closing = [r for r in range(srow + 1, send + 1)
                   if re.search(r"(closing|at the end|end of the (year|period)|balance as at (the )?end)", norm_caption(ctx.caption(nsh, r)))
                   and row_numbers(ctx, nsh, r)]
        if not closing:
            continue
        crow = closing[-1]
        pcols = periods_at(ctx, nsh, crow)
        for period, srow_soce in dated.items():
            if period not in pcols:
                continue
            a = loc(ctx, soce, srow_soce, col)
            b = loc(ctx, nsh, crow, pcols[period])
            if a.value is not None and b.value is not None:
                out.append((a, b, f"{reserve} – {period}"))
    return out


@check("XREF-02")
def check_duplicates(ctx: CheckContext) -> None:
    """Discover figures that appear twice (captions, context, value twins) and flag rounding twins."""
    # 1) user-confirmed map from earlier years
    if ctx.config.xref_map and Path(ctx.config.xref_map).exists():
        for p in load_xref_map(ctx.config.xref_map):
            a = _relocate(ctx, p.a, p.caption_a)
            b = _relocate(ctx, p.b, p.caption_b)
            if a is None or b is None or a.value is None or b.value is None:
                continue
            status = "ok" if same_figure(ctx, a, b) else mismatch_status(a.value, b.value)
            add_pair(ctx, a, b, status=status, source="map", confirmed=True)
            if status != "ok":
                _emit_twin(ctx, a, b, status, "confirmed cross-reference map")
    # 2) discovery by caption / context (SOCE reserves vs the other-equity note are an XREF-03 tie-out)
    idx = [e for e in build_index(ctx) if e.period is not None]
    covered = _note_scope(ctx)
    by_period: dict[str, list[NumEntry]] = defaultdict(list)
    for e in idx:
        by_period[e.period or ""].append(e)
    # captions repeated on a sheet (party names, "Opening balance" …) do not identify a figure
    freq: dict[tuple[str, str], int] = defaultdict(int)
    for e in idx:
        freq[(e.sheet, e.caption)] += 1
    for _period, entries in by_period.items():
        sig = [e for e in entries if _significant(e.caption) and freq[(e.sheet, e.caption)] <= 2]
        by_caption: dict[str, list[NumEntry]] = defaultdict(list)
        for e in sig:
            by_caption[e.caption].append(e)
        captions = list(by_caption)
        groups: list[list[NumEntry]] = []
        used: set[str] = set()
        for i, ca in enumerate(captions):
            if ca in used:
                continue
            grp = list(by_caption[ca])
            used.add(ca)
            for cb in captions[i + 1 :]:
                if cb not in used and fuzz.token_sort_ratio(ca, cb) >= 92:
                    grp.extend(by_caption[cb])
                    used.add(cb)
            if len(grp) > 1:
                groups.append(grp)
        for grp in groups:
            for i, ga in enumerate(grp):
                for gb in grp[i + 1 :]:
                    _consider(ctx, ga, gb, covered, source="caption")
        # 3) value twins on balance-type rows with the same subject
        twins = sorted((e for e in entries if _BALANCE_ROW.search(e.caption) and e.subject), key=lambda e: e.value)
        for i, ea in enumerate(twins):
            j = i + 1
            while j < len(twins) and twins[j].value - ea.value <= ROUNDING_TWIN:
                eb = twins[j]
                j += 1
                if ea.sheet == eb.sheet or ea.value == eb.value:
                    continue  # value twins are the same figure in two different statements / notes
                shared = (set(ea.subject.split()) & set(eb.subject.split())) - _WEAK_WORDS
                if len(shared) >= 2 and fuzz.token_set_ratio(ea.subject, eb.subject) >= 90:
                    _consider(ctx, ea, eb, covered, source="value")


def _already_reported(ctx: CheckContext, e: NumEntry) -> bool:
    addr = make_addr(e.row, e.col)
    return any(f.sheet == e.sheet and addr in f.cells and f.rule_id in ("XREF-01", "XREF-03") for f in ctx.findings)


def _consider(ctx: CheckContext, a: NumEntry, b: NumEntry, covered: dict[tuple[str, int], set[tuple[str, int, int]]], *, source: str) -> None:
    if a.key == b.key or (a.sheet == b.sheet and a.row == b.row):
        return
    seen: set[tuple[str, str, str, str]] = ctx.shared.get("xref_map_keys", set())
    ka, kb = (a.sheet, make_addr(a.row, a.col)), (b.sheet, make_addr(b.row, b.col))
    if (*ka, *kb) in seen or (*kb, *ka) in seen:
        return  # already compared (mandatory tie-out, link or an earlier pair)
    if _already_reported(ctx, a) or _already_reported(ctx, b):
        return
    if (a.value > 0) != (b.value > 0):
        return
    if _linked(ctx, a, b):
        return
    # face line vs its own note block: XREF-03 tie-out territory
    for x, y in ((a, b), (b, a)):
        cov = covered.get((x.sheet, x.row))
        if cov and (y.sheet, y.row, 0) in cov:
            return
    la, lb = Loc(a.sheet, a.row, a.col, a.value), Loc(b.sheet, b.row, b.col, b.value)
    status = mismatch_status(a.value, b.value)
    if status == "rounding_mismatch" and same_figure(ctx, la, lb):
        status = "ok"
    if status == "ok":
        add_pair(ctx, la, lb, status="ok", source=source)
        return
    if status == "rounding_mismatch":
        add_pair(ctx, la, lb, status=status, source=source)
        _emit_twin(ctx, la, lb, status, "same caption" if source == "caption" else "same context, value differs by a paisa")
        return
    add_pair(ctx, la, lb, status="proposed", source=source)


def _emit_twin(ctx: CheckContext, a: Loc, b: Loc, status: str, why: str) -> None:
    sa, sb = ctx.wb.sheet(a.sheet), ctx.wb.sheet(b.sheet)
    diff = (a.value or ZERO) - (b.value or ZERO)
    kind = "Rounding mismatch" if status == "rounding_mismatch" else "Mismatch"
    ctx.emit(
        "XREF-02",
        sheet=a.sheet,
        cells=[a.addr],
        caption=ctx.caption(sa, a.row),
        printed=ctx.printed_text(sa, a.row, a.col),
        expected=ctx.printed_text(sb, b.row, b.col),
        difference=diff,
        explanation=f"{kind}: {a.sheet}!{a.addr} shows {amt(a.value)} while {b.sheet}!{b.addr} shows {amt(b.value)} for the same figure ({why}).",
        evidence=[ev(ctx, a), ev(ctx, b)],
        suggested_fix="Link one figure to the other (or round both the same way) so the statements show one value.",
        key=f"{b.sheet}!{b.addr}",
    )


# =============================================================================== XREF-04


def _pdf_lines(path: Path) -> list[tuple[str, list[Decimal]]]:
    """Caption + amounts per printed line of a signed PDF (PyMuPDF, local)."""
    import pymupdf

    out: list[tuple[str, list[Decimal]]] = []
    with pymupdf.open(str(path)) as doc:  # type: ignore[no-untyped-call]
        for page in doc:
            lines: dict[int, list[tuple[float, str]]] = defaultdict(list)
            for w in page.get_text("words"):
                x0, y0, y1, text = w[0], w[1], w[3], w[4]
                lines[round((y0 + y1) / 4)].append((x0, text))  # 2-point line buckets
            for _y, ws in sorted(lines.items()):
                ws.sort()
                caption_parts: list[str] = []
                amounts: list[Decimal] = []
                for _x, text in ws:
                    num = parse_printed_number(text)
                    if num is not None and (("." in text) or text.strip() in ("-",) or "," in text):
                        amounts.append(num)
                    elif num is not None and not amounts and re.fullmatch(r"\d{1,2}", text):
                        continue  # note number column
                    elif not amounts:
                        caption_parts.append(text)
                if caption_parts and amounts:
                    out.append((" ".join(caption_parts), amounts))
    return out


def _signed_py_lines(ctx: CheckContext, path: Path) -> list[tuple[str, Decimal]]:
    """(normalised caption, current-year figure of the signed PY statements)."""
    out: list[tuple[str, Decimal]] = []
    if path.suffix.lower() == ".pdf":
        for cap, amounts in _pdf_lines(path):
            out.append((norm_caption(cap), amounts[0]))
        return out
    from fscore.config import EngagementConfig, PeriodSpec
    from fscore.io.xlsx_reader import Workbook

    cfg = ctx.config
    py_end = date(cfg.year_end.year - 1, cfg.year_end.month, cfg.year_end.day)
    shifted = [PeriodSpec(key=p.key, end=date(p.end.year - 1, p.end.month, p.end.day), months=p.months) for p in cfg.scope()]
    py_cfg = EngagementConfig.model_validate({**cfg.model_dump(), "year_end": py_end, "periods_in_scope": [p.model_dump() for p in shifted],
                                              "xref_map": None, "prior_year_signed": None})
    with Workbook.open(path) as wb:
        pctx = CheckContext(wb, py_cfg)
        roles = pctx.statements.roles
        for role in ("balance_sheet", "profit_loss", "cash_flow"):
            sname = roles.get(role)
            if not sname:
                continue
            sh = wb.sheet(sname)
            for row in range(1, sh.max_row + 1):
                pcols = period_columns_at(pctx, sh, row)
                col = pcols.get("CY")
                if col is None:
                    continue
                v = pctx.displayed(sh, row, col)
                cap = norm_caption(pctx.caption(sh, row))
                if v is not None and cap:
                    out.append((cap, v))
    return out


@check("XREF-04")
def check_previous_year(ctx: CheckContext) -> None:
    """Comparatives must equal the signed prior-year statements (or at least roll from them)."""
    path = ctx.prior_year_path or (Path(ctx.config.prior_year_signed) if ctx.config.prior_year_signed else None)
    if path is not None and Path(path).exists():
        _compare_signed(ctx, Path(path))
    _opening_balances(ctx)


def _compare_signed(ctx: CheckContext, path: Path) -> None:
    try:
        signed = _signed_py_lines(ctx, path)
    except Exception as exc:  # unreadable PY file: warn, never crash the run
        ctx.warnings.append(f"XREF-04: prior-year statements could not be read ({type(exc).__name__})")
        return
    if not signed:
        return
    by_cap: dict[str, list[Decimal]] = defaultdict(list)
    for cap, v in signed:
        if _significant(cap) or is_total_caption(cap):
            by_cap[cap].append(v)
    roles = ctx.statements.roles
    for role in ("balance_sheet", "profit_loss", "cash_flow"):
        sname = roles.get(role)
        if not sname:
            continue
        sh = ctx.wb.sheet(sname)
        seen_caps: dict[str, int] = defaultdict(int)
        for row in range(1, sh.max_row + 1):
            pcols = period_columns_at(ctx, sh, row)
            col = pcols.get("PY")
            if col is None or not has_number(ctx, sh, row, col) or not ctx.is_printed(sh, row, col):
                continue
            cap = norm_caption(ctx.caption(sh, row))
            if not cap:
                continue
            occurrence = seen_caps[cap]
            seen_caps[cap] += 1
            cands = by_cap.get(cap)
            if not cands or occurrence >= len(cands):
                continue
            ours = ctx.displayed(sh, row, col)
            theirs = cands[occurrence]
            if ours is None or ours == theirs:
                continue
            x = loc(ctx, sh, row, col)
            ctx.emit(
                "XREF-04",
                sheet=sh.name,
                cells=[x.addr],
                caption=ctx.caption(sh, row),
                printed=ctx.printed_text(sh, row, col),
                expected=fmt_amount(theirs),
                difference=ours - theirs,
                explanation=f"Previous-year figure {amt(ours)} differs from the signed prior-year statements ({amt(theirs)}).",
                evidence=[ev(ctx, x, "Comparative"), Evidence(label="Signed prior-year statements", printed=fmt_amount(theirs), value=theirs, note=path.name)],
                suggested_fix="Restore the comparative to the signed figure (or disclose a restatement under Ind AS 8).",
                period="PY",
                key=cap,
            )


def _opening_balances(ctx: CheckContext) -> None:
    """Without signed PY statements: the CY opening of the cash flow and SOCE must equal PY closing."""
    op_cy = line_loc(ctx, "cf_opening", "CY")
    cl_py = line_loc(ctx, "cf_closing", "PY")
    if op_cy is not None and cl_py is not None and op_cy.value is not None and cl_py.value is not None and not same_figure(ctx, op_cy, cl_py):
        target = link_target(ctx, ctx.wb.sheet(op_cy.sheet), op_cy.row, op_cy.col)
        if target is None or target[:3] != (cl_py.sheet, cl_py.row, cl_py.col):  # a link that prints differently is XREF-01
            ctx.emit(
                "XREF-04",
                sheet=op_cy.sheet,
                cells=[op_cy.addr],
                caption=ctx.caption(ctx.wb.sheet(op_cy.sheet), op_cy.row),
                printed=amt(op_cy.value),
                expected=amt(cl_py.value),
                difference=op_cy.value - cl_py.value,
                explanation=f"Cash and cash equivalents at the beginning of the year ({amt(op_cy.value)}) differ from the previous year's closing balance ({amt(cl_py.value)}).",
                evidence=[ev(ctx, op_cy, "CY opening"), ev(ctx, cl_py, "PY closing")],
                suggested_fix="Link the opening balance to the previous year's closing balance.",
                period="CY",
                key="cf_opening",
            )
