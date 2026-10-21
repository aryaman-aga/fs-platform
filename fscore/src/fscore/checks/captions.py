"""TXT-02 period labels, TXT-03 note numbering, TXT-04 captions vs signs, TXT-05 units (02 §6.6)."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal
from statistics import median

from fscore.checks.base import CheckContext, is_total_caption
from fscore.checks.hide_safety import allowed_row_dates
from fscore.engine.display import format_info
from fscore.engine.periods import find_dates, parse_period, scope_key
from fscore.engine.refs import make_addr
from fscore.engine.statements import header_labels, norm_caption
from fscore.io.xlsx_reader import Sheet
from fscore.models import CellRef, Certainty, Evidence, Severity
from fscore.registry import check

_STATEMENT_ROLES = ("balance_sheet", "profit_loss", "cash_flow", "soce")
_TITLE_RX = {
    "balance_sheet": re.compile(r"balance\s+sheet", re.I),
    "profit_loss": re.compile(r"profit\s+and\s+loss|statement\s+of\s+income", re.I),
    "cash_flow": re.compile(r"cash\s+flows?", re.I),
    "soce": re.compile(r"changes\s+in\s+equity", re.I),
    "notes": re.compile(r"notes?\s+(forming\s+part\s+of|to)\s+the\s+(standalone\s+|special\s+purpose\s+)?financial\s+statements", re.I),
}
_COMPARATIVE_RX = re.compile(r"\(\s*(?:as\s+at\s+)?([^():]{6,40}?\d{4})\s*:\s*[^)]*\)")
_PREV_YEAR_RX = re.compile(r"\((?:previous\s+year|py|prior\s+year)[^)]*\)", re.I)


def _ev(sh: Sheet, row: int, col: int, label: str, text: str | None = None) -> Evidence:
    return Evidence(label=label, ref=CellRef(sheet=sh.name, addr=make_addr(row, col)), printed=text)


def _title_cells(ctx: CheckContext, sh: Sheet, max_rows: int = 12) -> list[tuple[int, int, str]]:
    out = []
    for c in sh.iter_cells():
        if c.row > max_rows:
            break
        if c.is_text and ctx.is_printed(sh, c.row, c.col):
            out.append((c.row, c.col, str(c.value).strip()))
    return out


# ============================================================================ TXT-02


@check("TXT-02")
def check_period_labels(ctx: CheckContext) -> None:
    cfg = ctx.config
    year_end = cfg.year_end
    roles = ctx.statements.roles
    role_of_sheet = {v: k for k, v in roles.items() if k in _STATEMENT_ROLES}
    scope_ends = {p.end for p in cfg.scope()}
    allowed_rows = allowed_row_dates(cfg)
    # ---- statement titles
    for sh in ctx.print_sheets:
        role = role_of_sheet.get(sh.name)
        for r, c, text in _title_cells(ctx, sh):
            is_title = (role is not None and _TITLE_RX[role].search(text)) or _TITLE_RX["notes"].search(text)
            if not is_title:
                continue
            ds = find_dates(text)
            if not ds:
                continue
            match = next((x for x in ds if x[0] == year_end and not x[1]), None)
            d, bad, txt = match or ds[-1]
            low = text.lower()
            if bad:
                ctx.emit("TXT-02", sheet=sh.name, cells=[make_addr(r, c)], explanation=f"Heading contains an invalid date ('{txt}').",
                         printed=text, evidence=[_ev(sh, r, c, "Heading", text)], key=f"title-bad:{r}:{c}")
            elif d != year_end:
                ctx.emit("TXT-02", sheet=sh.name, cells=[make_addr(r, c)], severity=Severity.HIGH if role else Severity.MEDIUM,
                         explanation=f"Heading refers to {d:%B %d, %Y} but the reporting date is {year_end:%B %d, %Y}.",
                         printed=text, expected=f"{year_end:%B %d, %Y}", evidence=[_ev(sh, r, c, "Heading", text)],
                         suggested_fix=f"Correct the heading to {year_end:%B %d, %Y} (typed text, not a formula).", key=f"title-date:{r}:{c}")
            if role == "balance_sheet" and re.search(r"(year|period|quarter|months)\s+ended", low) and "as at" not in low:
                ctx.emit("TXT-02", sheet=sh.name, cells=[make_addr(r, c)], severity=Severity.LOW,
                         explanation="Balance Sheet heading says 'year ended'; a Balance Sheet is 'as at' the reporting date.",
                         printed=text, evidence=[_ev(sh, r, c, "Heading", text)], key=f"title-bs-wording:{r}:{c}")
            if role in ("profit_loss", "cash_flow") and re.search(r"\bas\s+at\b", low) and "ended" not in low:
                ctx.emit("TXT-02", sheet=sh.name, cells=[make_addr(r, c)],
                         explanation="Heading says 'as at' for a period statement; it should read 'for the year/period ended'.",
                         printed=text, evidence=[_ev(sh, r, c, "Heading", text)], key=f"title-pl-wording:{r}:{c}")
    # ---- column headers: 'As at' on P&L / CF, malformed dates, duplicated period headings
    for sh in ctx.print_sheets:
        role = role_of_sheet.get(sh.name)
        labels = header_labels(ctx, sh)
        tabs = ctx.tables(sh)
        first_data = min((t.r1 for t in tabs), default=sh.max_row + 1)
        for r, c, lab in labels:
            if not ctx.is_printed(sh, r, c):
                continue
            cell = sh.cell(r, c)
            text = lab.text
            if lab.malformed:
                ctx.emit("TXT-02", sheet=sh.name, cells=[make_addr(r, c)], explanation=f"Column heading '{text}' contains an invalid calendar date.",
                         printed=text, evidence=[_ev(sh, r, c, "Column heading", text)], key=f"hdr-bad:{r}:{c}")
            if role in ("profit_loss", "cash_flow") and r < first_data and lab.kind == "point" and re.search(r"\bas\s+(at|on)\b", text, re.I):
                ctx.emit("TXT-02", sheet=sh.name, cells=[make_addr(r, c)],
                         explanation=f"Column heading '{text}' uses 'As at' on a period statement; it should read 'Year ended …'.",
                         printed=text, evidence=[_ev(sh, r, c, "Column heading", text)],
                         suggested_fix="Use 'Year ended' / 'Three months ended' wording for profit and loss and cash flow columns.",
                         key=f"hdr-asat:{r}:{c}")
            _ = cell
        # duplicated period headings among printed columns of the same table
        for t in ctx.tables(sh):
            seen: dict[tuple[date | None, int | None, str], int] = {}
            for col in t.num_cols:
                if col == t.note_col or sh.col_hidden(col):
                    continue
                head = " ".join((t.headers.get(col) or "").split())
                hlab = parse_period(head) if head else None
                if hlab is None or hlab.end is None:
                    continue
                k = (hlab.end, hlab.months, head.lower())
                if k in seen:
                    hr = t.header_rows[-1] if t.header_rows else t.r1 - 1
                    ctx.emit("TXT-02", sheet=sh.name, cells=[make_addr(hr, seen[k]), make_addr(hr, col)],
                             explanation=f"Two printed columns carry the same heading '{head}'; one heading probably points at the wrong period "
                                         "(formula-linked heading).",
                             printed=head, evidence=[_ev(sh, hr, seen[k], "Column", head), _ev(sh, hr, col, "Column", head)],
                             suggested_fix="Fix the source heading once and replace the copied heading formula with typed text.",
                             key=f"hdr-dup:{t.r1}:{col}")
                else:
                    seen[k] = col
    # ---- captions: stale comparatives, stale years, malformed dates
    ends_sorted = sorted(scope_ends)
    py_years = {d.year for d in scope_ends}
    for sh in ctx.print_sheets:
        for cell in ctx.iter_printed_text(sh):
            text = str(cell.value).strip()
            if len(text) > 200:
                continue  # narrative paragraphs belong to NARR / GC
            for m in _COMPARATIVE_RX.finditer(text):
                ds = find_dates(m.group(1))
                if not ds:
                    continue
                d = ds[0][0]
                if d not in scope_ends:
                    ctx.emit("TXT-02", sheet=sh.name, cells=[cell.addr], severity=Severity.MEDIUM, certainty=Certainty.LIKELY,
                             explanation=f"Comparative in the caption refers to {d:%B %d, %Y}, which is not a reporting date in scope "
                                         f"({', '.join(f'{x:%b %d, %Y}' for x in ends_sorted)}).",
                             printed=text, evidence=[_ev(sh, cell.row, cell.col, "Caption", text)],
                             suggested_fix="Update the comparative date and amount to the previous reporting date.",
                             key=f"cap-comparative:{cell.addr}")
            for d, bad, txt in find_dates(text):
                if bad:
                    ctx.emit("TXT-02", sheet=sh.name, cells=[cell.addr], explanation=f"Caption contains an invalid calendar date ('{txt}').",
                             printed=text, evidence=[_ev(sh, cell.row, cell.col, "Caption", text)], key=f"cap-bad:{cell.addr}:{txt}")
                    continue
                if d in allowed_rows or d in scope_ends:
                    continue
                if ends_sorted and ends_sorted[0] - timedelta(days=366) < d <= ends_sorted[-1]:
                    continue  # dates inside the reporting periods (events, intermediate rows → MULTI-03)
                if _COMPARATIVE_RX.search(text):
                    continue  # already reported above
                row_has_numbers = any(c.row == cell.row and c.is_number for c in sh.iter_cells() if ctx.is_printed(sh, c.row, c.col))
                if d > ends_sorted[-1]:
                    if row_has_numbers and re.search(r"\b(as\s+at|balance|quarter|period|year)\b", text, re.I):
                        ctx.emit("TXT-02", sheet=sh.name, cells=[cell.addr], severity=Severity.MEDIUM, certainty=Certainty.LIKELY,
                                 explanation=f"Caption refers to {d:%B %d, %Y}, after the reporting date — a stale quarterly-template label?",
                                 printed=text, evidence=[_ev(sh, cell.row, cell.col, "Caption", text)], key=f"cap-future:{cell.addr}")
                elif row_has_numbers and d.year not in py_years and re.search(r"\b(as\s+at|balance|year\s+ended|rate)\b", text, re.I):
                    ctx.emit("TXT-02", sheet=sh.name, cells=[cell.addr], severity=Severity.LOW, certainty=Certainty.REVIEW,
                             explanation=f"Caption refers to {d:%B %d, %Y}, before the comparative period — stale year?",
                             printed=text, evidence=[_ev(sh, cell.row, cell.col, "Caption", text)], key=f"cap-old:{cell.addr}")
    _ = (Counter, defaultdict, scope_key)


# ============================================================================ TXT-03

_NOTE_HEADING = [
    re.compile(r"^\s*note\s*(?:no\.?\s*)?(\d{1,3})(?:\.(\d{1,2}))?\s*[:.\-–]?\s*(.*)$", re.I),
    re.compile(r"^\s*(\d{1,3})(?:\.(\d{1,2}))?\s*[.:)\-–]\s*([A-Za-z].{2,})$"),
]
_CONTD = re.compile(r"\(?\b(contd|cont'd|continued)\b\.?\)?", re.I)
_ITEM_LABEL = re.compile(r"^\s*\(([a-z]{1,4}|\d{1,2})\)\s*|^\s*([a-z]|[ivxlc]{1,6})[.)]\s+", re.I)
_ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}


def _roman(s: str) -> int | None:
    s = s.lower()
    if not s or any(ch not in _ROMAN for ch in s):
        return None
    total = 0
    for i, ch in enumerate(s):
        v = _ROMAN[ch]
        total += -v if i + 1 < len(s) and _ROMAN[s[i + 1]] > v else v
    return total


def _note_headings(ctx: CheckContext) -> list[tuple[int, str, int, int, str]]:
    """(note number, sheet, row, col, title) for every note heading, in print order."""
    out: list[tuple[int, str, int, int, str]] = []
    for idx, sh in enumerate(ctx.print_sheets):
        for c in sh.iter_cells():
            if c.col > 3 or not ctx.is_printed(sh, c.row, c.col):
                continue
            num = None
            title = ""
            if c.is_text:
                t = str(c.value).strip()
                if len(t) > 140 or _CONTD.search(t):
                    continue
                for rx in _NOTE_HEADING:
                    m = rx.match(t)
                    if m:
                        if m.group(2):
                            num = None  # sub-note 4.1
                            break
                        num, title = int(m.group(1)), m.group(3).strip()
                        break
            elif c.is_number and not c.is_formula and float(c.value).is_integer() and 1 <= c.value <= 99:
                nxt = sh.cell(c.row, c.col + 1)
                if nxt is not None and nxt.is_text and str(nxt.value).strip() and sh.is_bold(nxt) and len(str(nxt.value)) < 120 \
                        and not _CONTD.search(str(nxt.value)):
                    num, title = int(c.value), str(nxt.value).strip()
            if num is not None and title and not re.match(r"^(to|and|of|the)\b", title, re.I):
                out.append((num, sh.name, c.row, c.col, title))
        _ = idx
    order = {s.name: i for i, s in enumerate(ctx.print_sheets)}
    out.sort(key=lambda x: (order[x[1]], x[2]))
    return out


@check("TXT-03")
def check_note_numbering(ctx: CheckContext) -> None:
    smap = ctx.statements
    heads = _note_headings(ctx)
    found = {n for n, *_ in heads}
    # references on the face statements must exist
    for num, locs in sorted(smap.note_refs.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0):
        if not num.isdigit() or int(num) in found or not heads:
            continue
        sname, row = locs[0]
        ctx.emit("TXT-03", sheet=sname, cells=[make_addr(row, 1)], severity=Severity.MEDIUM, certainty=Certainty.LIKELY,
                 explanation=f"Note {num} is referenced on the face of {sname} but no Note {num} heading was found in the notes.",
                 key=f"ref-missing:{num}")
    # each note once
    by_num: dict[int, list[tuple[str, int, int, str]]] = defaultdict(list)
    for n, s, r, c, t in heads:
        by_num[n].append((s, r, c, t))
    for n, occ in by_num.items():
        titles = {norm_caption(t) for _s, _r, _c, t in occ}
        if len(occ) > 1 and len(titles) > 1:
            s, r, c, t = occ[1]
            ctx.emit("TXT-03", sheet=s, cells=[make_addr(r, c)], severity=Severity.MEDIUM,
                     explanation=f"Note number {n} is used for more than one note ('{occ[0][3]}' and '{t}').",
                     evidence=[Evidence(label=x[3], ref=CellRef(sheet=x[0], addr=make_addr(x[1], x[2]))) for x in occ],
                     suggested_fix="Renumber the notes so each number appears once and in sequence.", key=f"dup:{n}")
    # sequence: increasing, no gaps
    seq = [n for n, *_ in heads]
    for i in range(1, len(seq)):
        if seq[i] < seq[i - 1] and seq[i] not in seq[:i]:
            n, s, r, c, _t = heads[i]
            ctx.emit("TXT-03", sheet=s, cells=[make_addr(r, c)], severity=Severity.LOW, certainty=Certainty.LIKELY,
                     explanation=f"Note {n} appears after Note {seq[i - 1]}; notes are out of sequence.", key=f"order:{n}")
    if len(found) >= 3:
        lo, hi = min(found), max(found)
        missing = [n for n in range(lo, hi + 1) if n not in found]
        if missing and len(missing) <= max(3, (hi - lo) // 4):
            ctx.emit("TXT-03", sheet=heads[-1][1], severity=Severity.LOW, certainty=Certainty.REVIEW,
                     explanation=f"Note numbering has gaps: {', '.join(str(m) for m in missing)} not found between Note {lo} and Note {hi}.",
                     key=f"gaps:{','.join(map(str, missing))}")
    # sub-item numbering inside each note: duplicates / gaps within one list
    _check_item_labels(ctx, heads)


def _label_kind(lab: str, last_alpha: int | None, last_roman: int | None) -> tuple[str, int] | None:
    """('alpha'|'roman'|'num', ordinal); i / v / x / l / c are read from the list they continue."""
    if lab.isdigit():
        return ("num", int(lab))
    low = lab.lower()
    rom = _roman(low)
    alpha = ord(low) - 96 if len(low) == 1 and low.isalpha() else None
    if rom is not None and alpha is not None:
        if last_alpha is not None and alpha == last_alpha + 1:
            return ("alpha", alpha)
        if last_roman is not None and rom == last_roman + 1:
            return ("roman", rom)
        return ("roman", rom) if low == "i" else ("alpha", alpha)
    if rom is not None:
        return ("roman", rom)
    if alpha is not None:
        return ("alpha", alpha)
    return None


def _check_item_labels(ctx: CheckContext, heads: list[tuple[int, str, int, int, str]]) -> None:
    """Sub-item labels inside each note: a label repeated within one list, or a skipped label.

    A first label ('(a)', '(i)', '(1)') restarts its list once the list has moved on,
    so separate tables in one note may each number from '(i)'.
    """
    by_sheet: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for n, s, r, _c, _t in heads:
        by_sheet[s].append((r, n))
    for sh in ctx.print_sheets:
        starts = sorted(by_sheet.get(sh.name, []))
        if not starts:
            continue
        bounds = [(r, starts[i + 1][0] - 1 if i + 1 < len(starts) else sh.max_row, n) for i, (r, n) in enumerate(starts)]
        for r1, r2, note in bounds:
            lists: dict[str, list[tuple[int, int, str]]] = {}
            for row in range(r1 + 1, r2 + 1):
                cell = None
                for col in (1, 2, 3):
                    c = sh.cell(row, col)
                    if c is not None and c.is_text and str(c.value).strip():
                        cell = c
                        break
                if cell is None or not ctx.is_printed(sh, cell.row, cell.col):
                    continue
                m = _ITEM_LABEL.match(str(cell.value))
                if not m:
                    continue
                lab = m.group(1) or m.group(2)
                la = lists["alpha"][-1][0] if lists.get("alpha") else None
                lr = lists["roman"][-1][0] if lists.get("roman") else None
                kind = _label_kind(lab, la, lr)
                if kind is None:
                    continue
                k, ordv = kind
                if k == "alpha":
                    lists.pop("roman", None)
                    lists.pop("num", None)
                elif k == "roman":
                    lists.pop("num", None)
                cur = lists.setdefault(k, [])
                if ordv == 1 and cur and cur[-1][0] >= 2:
                    cur.clear()  # a new list starts
                if any(o == ordv for o, _r, _l in cur):
                    first = next(rr for o, rr, _l in cur if o == ordv)
                    ctx.emit("TXT-03", sheet=sh.name, cells=[make_addr(first, cell.col), make_addr(row, cell.col)], severity=Severity.MEDIUM,
                             certainty=Certainty.LIKELY,
                             explanation=f"Item label '({lab})' is used twice within Note {note}.",
                             evidence=[_ev(sh, first, cell.col, "First"), _ev(sh, row, cell.col, "Repeated", str(cell.value))],
                             suggested_fix="Renumber the sub-items in sequence.", key=f"item-dup:{note}:{row}")
                elif cur and ordv > cur[-1][0] + 1 and k != "num":
                    ctx.emit("TXT-03", sheet=sh.name, cells=[make_addr(row, cell.col)], severity=Severity.LOW, certainty=Certainty.REVIEW,
                             explanation=f"Item labels in Note {note} skip from '({cur[-1][2]})' to '({lab})'.",
                             key=f"item-gap:{note}:{row}")
                cur.append((ordv, row, lab))


# ============================================================================ TXT-04

_PROCEEDS = re.compile(r"^\s*(proceeds|receipts?)\b", re.I)
_REPAY = re.compile(r"^\s*(repayment|payment|purchase|acquisition|dividends?\s+paid|interest\s+paid|taxes?\s+paid|income\s+taxes?\s+paid)\b", re.I)
_LESS = re.compile(r"^\s*less\s*[:\-–]", re.I)
_MOVEMENT_WORDS = re.compile(r"^\s*(additions?|disposals?|deletions?|charge\s+for\s+the\s+(year|period)|depreciation\s+for\s+the\s+(year|period)|transfers?|adjustments?)\b", re.I)


@check("TXT-04")
def check_captions_vs_signs(ctx: CheckContext) -> None:
    roles = ctx.statements.roles
    cf = roles.get("cash_flow")
    face = {roles[r] for r in _STATEMENT_ROLES if r in roles}
    for sh in ctx.print_sheets:
        tables = ctx.tables(sh)
        for t in tables:
            cols = [c for c in t.num_cols if c != t.note_col]
            first_num = min(cols) if cols else None
            for row in t.rows:
                cap = ctx.caption(sh, row, before_col=first_num)
                ncap = norm_caption(cap)
                vals: dict[int, Decimal] = {}
                for c in cols:
                    dv = ctx.displayed(sh, row, c) if ctx.is_printed(sh, row, c) else None
                    if dv is not None and dv != 0:
                        vals[c] = dv
                if sh.name == cf and vals:
                    has_inc = re.search(r"\bincrease\b", ncap) is not None
                    has_dec = re.search(r"\bdecrease\b", ncap) is not None
                    if _PROCEEDS.search(ncap) or (has_inc and not has_dec and ncap.startswith("increase")):
                        neg = [c for c, v in vals.items() if v < 0]
                        if neg:
                            ctx.emit("TXT-04", sheet=sh.name, cells=[make_addr(row, t.caption_col)] + [make_addr(row, c) for c in neg],
                                     explanation=f"'{cap}' shows an outflow; the caption describes an inflow.",
                                     printed=ctx.printed_text(sh, row, neg[0]), evidence=[ctx.evidence_for(sh, row, c) for c in neg],
                                     key=f"proceeds:{row}")
                    if _REPAY.search(ncap):
                        pos = [c for c, v in vals.items() if v > 0]
                        if pos:
                            ctx.emit("TXT-04", sheet=sh.name, cells=[make_addr(row, t.caption_col)] + [make_addr(row, c) for c in pos],
                                     explanation=f"'{cap}' shows an inflow; the caption describes an outflow.",
                                     printed=ctx.printed_text(sh, row, pos[0]), evidence=[ctx.evidence_for(sh, row, c) for c in pos],
                                     key=f"repay:{row}")
                if _LESS.search(cap) and vals:
                    _less_adds(ctx, sh, row, cap, vals)
                if _MOVEMENT_WORDS.search(ncap) and not is_total_caption(ncap) and (
                        _sums_opening(ctx, sh, row, cols) or _closes_rollforward(ctx, sh, t, row)):
                    ctx.emit("TXT-04", sheet=sh.name, cells=[make_addr(row, 1)], severity=Severity.MEDIUM, certainty=Certainty.LIKELY,
                             explanation=f"Row captioned '{cap}' is a closing balance (it adds the opening balance and the movements).",
                             suggested_fix="Caption the row 'As at <closing date>' / 'Closing balance'.", key=f"closing-label:{row}")
            # totals without a caption (face statements, or where other totals of the table are captioned)
            captioned = [r for r in t.total_rows if ctx.caption(sh, r, before_col=first_num)]
            for row in t.total_rows:
                if ctx.caption(sh, row, before_col=first_num):
                    continue
                if not (sh.name in face or captioned):
                    continue
                if not any(ctx.is_printed(sh, row, c) and (sh.cell(row, c) is not None and sh.cell(row, c).is_formula) for c in cols):  # type: ignore[union-attr]
                    continue
                ctx.emit("TXT-04", sheet=sh.name, cells=[make_addr(row, cols[0])] if cols else [], severity=Severity.LOW, certainty=Certainty.REVIEW,
                         explanation="Total row without a caption.", suggested_fix="Caption the total (e.g. 'Total').", key=f"nocap:{row}")


def _less_adds(ctx: CheckContext, sh: Sheet, row: int, cap: str, vals: dict[int, Decimal]) -> None:
    for cell in sh.iter_cells():
        if cell.formula is None or cell.row <= row or not ctx.is_printed(sh, cell.row, cell.col) or cell.col not in vals:
            continue
        lf = ctx.linear(sh, cell)
        if lf is None or not lf.is_total:
            continue
        for t in lf.terms:
            if t.sheet != sh.name or not (t.area.r1 <= row <= t.area.r2 and t.area.c1 <= cell.col <= t.area.c2):
                continue
            v = vals[cell.col]
            if t.coef > 0 and v > 0:
                ctx.emit("TXT-04", sheet=sh.name, cells=[make_addr(row, cell.col), cell.addr], severity=Severity.MEDIUM, certainty=Certainty.LIKELY,
                         explanation=f"'{cap}' is shown as a positive figure and the total {cell.addr} adds it; a 'Less' row must reduce the total.",
                         evidence=[ctx.evidence_for(sh, row, cell.col), ctx.evidence_for(sh, cell.row, cell.col, "Total")],
                         suggested_fix="Show the amount in brackets or subtract it in the total.", key=f"less:{row}:{cell.col}")
            return


_OPENING_CAP = re.compile(r"^(balance\s+)?(as\s+at|as\s+on)\b|^opening|beginning\s+of")


def _closes_rollforward(ctx: CheckContext, sh: Sheet, t: object, row: int) -> bool:
    """Last row of a movement table that starts with an opening balance, and whose
    figures equal opening + the rows in between (a closing row, whatever its caption)."""
    rows = sorted(getattr(t, "rows", []))
    if not rows or row != rows[-1] or len(rows) < 3:
        return False
    first_num = min(getattr(t, "num_cols", []) or [1])
    opening = next((r for r in rows[:-1] if _OPENING_CAP.search(norm_caption(ctx.caption(sh, r, before_col=first_num)))), None)
    if opening is None:
        return False
    checked = False
    for col in getattr(t, "num_cols", []):
        if col == getattr(t, "note_col", None):
            continue
        close = ctx.displayed(sh, row, col)
        if close is None:
            continue
        total = sum((ctx.displayed_or_zero(sh, r, col) for r in rows if opening <= r < row), Decimal(0))
        if total != close:
            return False
        checked = True
    return checked


def _sums_opening(ctx: CheckContext, sh: Sheet, row: int, cols: list[int]) -> bool:
    """Row's formulas add rows above that include an opening ('As at' / 'Balance') row."""
    for col in cols:
        cell = sh.cell(row, col)
        if cell is None or cell.formula is None:
            continue
        lf = ctx.linear(sh, cell)
        if lf is None or not lf.is_total:
            continue
        rows = {r for t in lf.terms if t.sheet == sh.name for r in range(t.area.r1, t.area.r2 + 1)}
        if any(re.search(r"^(balance\s+)?(as\s+at|as\s+on)\b|opening|beginning", norm_caption(ctx.caption(sh, r))) for r in rows):
            return True
    return False


# ============================================================================ TXT-05

UNIT_LINE_RX = re.compile(
    r"all\s+amounts?\s+(?:are\s+)?in|amounts?\s+in\s+(?:₹|rs\.?|inr|indian)|\(\s*(?:₹|rs\.?|inr)\s*(?:in\s+)?(?:lakhs?|lacs?|millions?|mn|crores?|thousands?|'000)"
    r"|(?:₹|rs\.?|inr)\s+in\s+(?:lakhs?|lacs?|millions?|mn|crores?|thousands?)|(?:lakhs?|lacs?|millions?|crores?)\s+of\s+(?:indian\s+)?rupees",
    re.I,
)
UNIT_WORDS = [
    ("lakhs", re.compile(r"\blakhs?\b|\blacs?\b", re.I)),
    ("millions", re.compile(r"\bmillions?\b|\bmn\b", re.I)),
    ("crores", re.compile(r"\bcrores?\b|\bcr\b", re.I)),
    ("thousands", re.compile(r"\bthousands?\b|'000", re.I)),
]


def unit_of(text: str) -> str | None:
    for unit, rx in UNIT_WORDS:
        if rx.search(text):
            return unit
    if re.search(r"rupees|₹|\binr\b", text, re.I):
        return "rupees"
    return None


@check("TXT-05")
def check_units(ctx: CheckContext) -> None:
    expected = ctx.config.units
    sheet_units: dict[str, list[tuple[str, int, int, str]]] = {}
    for sh in ctx.print_sheets:
        has_numbers = any(True for _ in ctx.iter_printed_numbers(sh))
        found: list[tuple[str, int, int, str]] = []
        for cell in ctx.iter_printed_text(sh):
            text = str(cell.value)
            if len(text) > 160 or not UNIT_LINE_RX.search(text):
                continue
            u = unit_of(text)
            if u:
                found.append((u, cell.row, cell.col, text.strip()))
        sheet_units[sh.name] = found
        if has_numbers and not found and _amount_like(ctx, sh):
            ctx.emit("TXT-05", sheet=sh.name, explanation=f"No unit line ('All amounts in {expected} …') found on {sh.name}.",
                     suggested_fix=f"Add '(All amounts in ₹ {expected}, unless otherwise stated)' below the heading.", key=f"missing:{sh.name}")
        for u, r, c, text in found:
            if u != expected:
                ctx.emit("TXT-05", sheet=sh.name, cells=[make_addr(r, c)], severity=Severity.MEDIUM, certainty=Certainty.LIKELY,
                         explanation=f"Unit line says '{u}' but the statements are presented in {expected}.",
                         printed=text, expected=expected, evidence=[_ev(sh, r, c, "Unit line", text)], key=f"unit:{r}:{c}")
        # tables headed in lakhs/millions whose 2-dp figures are rupee-sized
        if found and expected in ("lakhs", "millions", "crores"):
            for t in ctx.tables(sh):
                vals = []
                for row in t.rows:
                    cap = ctx.caption(sh, row, before_col=min(t.num_cols))
                    if re.search(r"shares?|number|no\.|nos|%|ratio|rate|days|times|per\s+share|face\s+value", cap, re.I):
                        continue
                    for col in t.num_cols:
                        tcell = sh.cell(row, col)
                        if tcell is None or not tcell.is_number or not ctx.is_printed(sh, row, col):
                            continue
                        if format_info(sh.number_format(tcell)).decimals < 2:
                            continue
                        v = abs(float(tcell.value))
                        if v:
                            vals.append(v)
                if len(vals) >= 3 and median(vals) >= 1e6 and all(float(x).is_integer() for x in vals[:20]):
                    ctx.emit("TXT-05", sheet=sh.name, cells=[make_addr(t.r1, min(t.num_cols))], severity=Severity.LOW, certainty=Certainty.REVIEW,
                             explanation=f"Table '{t.title or 'at row ' + str(t.r1)}' is presented in {expected} but its figures look like rupees "
                                         f"(median {median(vals):,.0f}).", key=f"rupees:{t.r1}")


def _amount_like(ctx: CheckContext, sh: Sheet) -> bool:
    """Sheet prints at least a few 2-dp amounts (not only note numbers or share counts)."""
    n = 0
    for c in ctx.iter_printed_numbers(sh):
        if format_info(sh.number_format(c)).decimals >= 2 and format_info(sh.number_format(c)).kind == "number":
            n += 1
            if n >= 3:
                return True
    return False
