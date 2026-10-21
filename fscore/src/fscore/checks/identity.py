"""TXT-01 — signatories, DIN / CIN / FRN / membership numbers, signing date and place (02 §6.6).

A signatory master is built from the Balance Sheet signature block
(name → role → DIN / membership no.) and every signature block on every page
(BS, P&L, cash flow, SOCE, last notes page) is validated against it:

* DIN is 8 digits; the same person has the same DIN everywhere; two different
  people never share a DIN (the error actually found in practice);
* membership no. 6 digits, FRN ``\\d{6}[A-Z]``, CIN 21 characters and identical;
* names identical across pages (one-letter variants reported, never auto-fixed);
* signing date present, identical, after the period end and in the window;
  place present; placeholders ("DIN: XXX", "Date: XXXXXX", "[Name]") are High
  (control C8);
* fixed words (Director, Chartered Accountants …) spelled correctly.

Layout-agnostic: blocks are found from their content (roles, "DIN", "Place",
"Date" …), stacked vertically in a column or within one wrapped cell.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from rapidfuzz import fuzz

from fscore.checks.base import CheckContext
from fscore.engine.display import is_date_format, serial_to_datetime
from fscore.engine.periods import find_dates
from fscore.engine.refs import make_addr
from fscore.io.xlsx_reader import Sheet
from fscore.models import CellRef, Certainty, Evidence, FixKind, Severity
from fscore.registry import check

# ---------------------------------------------------------------------------- patterns

DIN_RX = re.compile(r"\bDIN\b\.?\s*(?:No\.?|Number|#)?\s*[:\-–.]?\s*(.*)$", re.I)
MEM_RX = re.compile(r"\b(?:Membership|Mem\.)\s*(?:No\.?|Number|#)?\s*[:\-–.]?\s*(.*)$|\bM\.\s*No\.?\s*[:\-–.]?\s*(.*)$", re.I)
FRN_RX = re.compile(r"\b(?:Firm(?:['’]s)?\s+Reg(?:istration|n)\.?\s*(?:No\.?|Number)?|FRN)\s*[:\-–.]?\s*(.*)$", re.I)
CIN_RX = re.compile(r"\bCIN\b\.?\s*(?:No\.?)?\s*[:\-–.]?\s*(.*)$", re.I)
DATE_RX = re.compile(r"^\s*(?:Date|Dated)\s*[:\-–.]?\s*(?!of\b|on\b|when\b|from\b)(.*)$", re.I)
PLACE_RX = re.compile(r"^\s*Place\s*[:\-–.]?\s*(?!of\b)(.*)$", re.I)
CIN_FORMAT = re.compile(r"^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$")
FRN_FORMAT = re.compile(r"^\d{6}[A-Z](?:\s*/\s*[A-Z]\d{6})?$")
BLOCK_HINT_RX = re.compile(
    r"for\s+and\s+on\s+behalf|as\s+per\s+our\s+report|chartered\s+accountants?|board\s+of\s+directors|"
    r"\bfor\s+.+\s+(?:&|and)\s+co\b|\bllp\b.*chartered|^for\s+[A-Z]",
    re.I,
)

CANON_ROLES = [
    "Director", "Managing Director", "Whole-time Director", "Executive Director", "Non-Executive Director",
    "Independent Director", "Nominee Director", "Additional Director", "Chairman", "Chairperson", "Partner",
    "Proprietor", "Chief Financial Officer", "Chief Executive Officer", "Company Secretary", "Manager",
]
_ROLE_ABBR = {"cfo": "Chief Financial Officer", "ceo": "Chief Executive Officer", "cs": "Company Secretary"}
DIRECTOR_ROLES = {r for r in CANON_ROLES if "Director" in r or r in ("Chairman", "Chairperson")}
ICAI_ROLES = {"Partner", "Proprietor"}

_PLACEHOLDER_RX = re.compile(
    r"^(?:\[.*\]|<.*>|\{.*\}|tbd|tba|to\s+be\s+(?:filled|updated|confirmed|inserted)|n/?a|nil|"
    r"(?:dd|d)[\s/.\-]+(?:mm|m|mmm)[\s/.\-]+(?:yyyy|yy)|xx+.*|.*xxx+.*|name(?:\s+of\s+.*)?|director\s+name|signature)$",
    re.I,
)
_NAME_STOP = re.compile(
    r"\b(for|and|on|behalf|board|directors?|limited|ltd|private|pvt|llp|co|company|chartered|accountants?|report|even|"
    r"date|dated|place|statement|notes?|financial|as|per|our|of|the|cin|din|membership|registration|frn|firm|signature|"
    r"signed|authori[sz]ed|signatory|total|amount|particulars|year|ended|lakhs?|rupees|crores?|million)\b",
    re.I,
)


def is_placeholder(value: str) -> bool:
    t = (value or "").strip().strip("()[]").strip(" .,:;-–")
    if not t:
        return True
    raw = (value or "").strip()
    if re.fullmatch(r"[\[<{].*[\]>}]", raw):
        return True
    if re.fullmatch(r"[xX*#_.\-?–]+", t):
        return True
    return bool(_PLACEHOLDER_RX.match(t))


def clean_value(v: str) -> str:
    t = (v or "").strip()
    t = re.sub(r"^[(\[]\s*", "", t)
    t = re.sub(r"\s*[)\]]\s*$", "", t)
    return t.strip(" ,;:")


def role_of(text: str) -> str | None:
    """Canonical role if the line is (essentially) a designation, fuzzy for misspellings."""
    t = re.sub(r"\(.*?\)", " ", text)
    t = re.sub(r"\bDIN\b.*$|\bMembership\b.*$|\bM\.\s*No\..*$", " ", t, flags=re.I)
    t = " ".join(t.replace("–", "-").split()).strip(" ,;:-.").lower()
    if not t or len(t) > 45:
        return None
    if t in _ROLE_ABBR:
        return _ROLE_ABBR[t]
    norm = t.replace("wholetime", "whole-time").replace("whole time", "whole-time").replace("non executive", "non-executive")
    for r in CANON_ROLES:
        if norm == r.lower():
            return r
    best = max(CANON_ROLES, key=lambda r: fuzz.ratio(norm, r.lower()))
    if fuzz.ratio(norm, best.lower()) >= 85:
        return best
    return None


def looks_like_name(text: str) -> bool:
    t = (text or "").strip().strip(",;:")
    if not t or len(t) > 60 or any(ch.isdigit() for ch in t) or ":" in t or "(" in t:
        return False
    if _NAME_STOP.search(t) or role_of(t):
        return False
    words = t.replace(".", ". ").split()
    if not 1 <= len(words) <= 6:
        return False
    if sum(ch.isalpha() for ch in t) < 3:
        return False
    caps = sum(1 for w in words if w[0].isupper())
    return caps >= max(1, len(words) - 1)


def norm_name(name: str) -> str:
    t = re.sub(r"[.,']", " ", name or "").lower()
    t = re.sub(r"^(mr|mrs|ms|dr|ca|cs)\s+", "", " ".join(t.split()))
    return " ".join(t.split())


def same_person(a: str, b: str) -> bool:
    return norm_name(a) == norm_name(b)


def digits(value: str) -> str:
    return re.sub(r"[\s\-]", "", clean_value(value))


# ---------------------------------------------------------------------------- block model


@dataclass(frozen=True)
class Line:
    sheet: str
    row: int
    col: int
    idx: int
    text: str
    printed: bool

    @property
    def addr(self) -> str:
        return make_addr(self.row, self.col)


@dataclass
class SigEntry:
    sheet: str
    role: str | None = None
    role_line: Line | None = None
    name: str | None = None
    name_line: Line | None = None
    din: str | None = None
    din_line: Line | None = None
    membership: str | None = None
    mem_line: Line | None = None

    @property
    def printed(self) -> bool:
        lines = [x for x in (self.role_line, self.name_line, self.din_line, self.mem_line) if x is not None]
        return bool(lines) and all(x.printed for x in lines)

    @property
    def anchor(self) -> Line | None:
        return self.din_line or self.mem_line or self.role_line or self.name_line


@dataclass
class SheetSig:
    sheet: str
    entries: list[SigEntry] = field(default_factory=list)
    cin: list[tuple[str, Line]] = field(default_factory=list)
    frn: list[tuple[str, Line]] = field(default_factory=list)
    dates: list[tuple[str, Line]] = field(default_factory=list)
    places: list[tuple[str, Line]] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)  # lines in signature regions
    cells: set[tuple[int, int]] = field(default_factory=set)

    @property
    def has_block(self) -> bool:
        return bool(self.entries or self.dates or self.cin or self.frn)


def sheet_lines(ctx: CheckContext, sheet: Sheet) -> list[Line]:
    key = f"txt:lines:{sheet.name}"
    if key in ctx.shared:
        return ctx.shared[key]  # type: ignore[no-any-return]
    out: list[Line] = []
    for c in sheet.iter_cells():
        if not c.is_text or not str(c.value).strip():
            continue
        printed = ctx.is_printed(sheet, c.row, c.col)
        for i, part in enumerate(str(c.value).splitlines()):
            t = part.strip()
            if t:
                out.append(Line(sheet.name, c.row, c.col, i, t, printed))
    ctx.shared[key] = out
    return out


_LABEL_LINE = re.compile(r"^\s*(date|dated|place|din|cin|membership|m\.\s*no|frn|firm)\b", re.I)


def _value_right(ctx: CheckContext, sheet: Sheet, line: Line) -> str:
    """Value written in the next cell(s) to the right of a label ('Date:' | 15-May-2026).

    A neighbouring cell that is itself a labelled line (another signatory's
    'Date: …') is not this label's value.
    """
    for cc in (line.col + 1, line.col + 2):
        c = sheet.cell(line.row, cc)
        if c is None or c.value is None or (isinstance(c.value, str) and not c.value.strip()):
            continue
        if isinstance(c.value, str) and _LABEL_LINE.match(c.value):
            return ""
        if c.is_number and is_date_format(sheet.number_format(c)):
            dt = serial_to_datetime(float(c.value), ctx.wb.date1904)
            return dt.strftime("%B %d, %Y") if dt else ""
        return ctx.wb.render_cell(c).clean if not isinstance(c.value, str) else str(c.value).strip()
    return ""


_TOKEN_RX = {
    # identifiers are single tokens: never let a match run on into the following sentence
    "din": re.compile(r"\s*(\[[^\]]{0,30}\]|<[^>]{0,30}>|[A-Za-z0-9*#_?.\-]{1,16}(?:\s\d{4}\b)?)"),
    "cin": re.compile(r"\s*(\[[^\]]{0,30}\]|<[^>]{0,30}>|[A-Za-z0-9*#_?.\-]{1,21})"),
    "frn": re.compile(r"\s*(\[[^\]]{0,30}\]|<[^>]{0,30}>|\d{6}[A-Za-z](?:\s*/\s*[A-Za-z]\d{6})?|[A-Za-z0-9*#_?.\-/]{1,16})"),
    "mem": re.compile(r"\s*(\[[^\]]{0,30}\]|<[^>]{0,30}>|[A-Za-z0-9*#_?.\-]{1,12})"),
}


def _bounded(value: str, kind: str | None) -> str:
    if kind is None:
        return value[:60]
    m = _TOKEN_RX[kind].match(value)
    return m.group(1).strip().rstrip(".") if m else ""


def _label_value(ctx: CheckContext, sheet: Sheet, rx: re.Pattern[str], line: Line) -> str | None:
    m = rx.search(line.text)
    if not m:
        return None
    val = next((g for g in m.groups() if g is not None), "") or ""
    kind = {id(DIN_RX): "din", id(CIN_RX): "cin", id(FRN_RX): "frn", id(MEM_RX): "mem"}.get(id(rx))

    def norm(v: str) -> str:
        raw = v.strip()
        if raw[:1] in ("[", "<", "{"):
            return raw[:40]  # bracketed placeholder such as [DIN] / <CIN>
        return clean_value(_bounded(clean_value(raw), kind))

    val = norm(val)
    if not val:
        right = _value_right(ctx, sheet, line)
        val = norm(right) if right else ""
    return val


def parse_sheet_signatures(ctx: CheckContext, sheet: Sheet) -> SheetSig:
    key = f"txt:sig:{sheet.name}"
    if key in ctx.shared:
        return ctx.shared[key]  # type: ignore[no-any-return]
    lines = sheet_lines(ctx, sheet)
    by_cell: dict[tuple[int, int], list[Line]] = defaultdict(list)
    for ln in lines:
        by_cell[(ln.row, ln.col)].append(ln)

    def above(ln: Line) -> Line | None:
        prev = [x for x in by_cell[(ln.row, ln.col)] if x.idx < ln.idx]
        if prev:
            return prev[-1]
        for r in range(ln.row - 1, max(0, ln.row - 4), -1):
            lst = by_cell.get((r, ln.col))
            if lst:
                return lst[-1]
        return None

    def below(ln: Line, n: int = 3) -> list[Line]:
        out = [x for x in by_cell[(ln.row, ln.col)] if x.idx > ln.idx]
        r = ln.row + 1
        while len(out) < n and r <= ln.row + 4:
            out.extend(by_cell.get((r, ln.col), []))
            r += 1
        return out[:n]

    sig = SheetSig(sheet.name)
    consumed: set[Line] = set()
    # entries anchored on designation lines
    for ln in lines:
        role = role_of(ln.text)
        if role is None:
            continue
        e = SigEntry(sheet.name, role=role, role_line=ln)
        if DIN_RX.search(ln.text):
            e.din, e.din_line = _label_value(ctx, sheet, DIN_RX, ln), ln
        elif MEM_RX.search(ln.text):
            e.membership, e.mem_line = _label_value(ctx, sheet, MEM_RX, ln), ln
        for nxt in below(ln):
            if e.din_line is None and DIN_RX.search(nxt.text):
                e.din, e.din_line = _label_value(ctx, sheet, DIN_RX, nxt), nxt
            elif e.mem_line is None and MEM_RX.search(nxt.text):
                e.membership, e.mem_line = _label_value(ctx, sheet, MEM_RX, nxt), nxt
            if role_of(nxt.text):
                break
        up = above(ln)
        if (up is not None and not role_of(up.text) and not DIN_RX.search(up.text)
                and (looks_like_name(up.text) or is_placeholder(up.text) or re.match(r"^\[?\s*name", up.text, re.I))):
            e.name, e.name_line = up.text.strip(), up
        sig.entries.append(e)
        consumed.update(x for x in (e.role_line, e.din_line, e.mem_line, e.name_line) if x is not None)
    # DIN / membership lines without a recognisable designation
    for ln in lines:
        if ln in consumed:
            continue
        if DIN_RX.search(ln.text) and not CIN_RX.search(ln.text):
            e = SigEntry(sheet.name, din=_label_value(ctx, sheet, DIN_RX, ln), din_line=ln)
        elif MEM_RX.search(ln.text):
            e = SigEntry(sheet.name, membership=_label_value(ctx, sheet, MEM_RX, ln), mem_line=ln)
        else:
            continue
        up = above(ln)
        if up is not None and looks_like_name(up.text):
            e.name, e.name_line = up.text.strip(), up
        sig.entries.append(e)
        consumed.update(x for x in (e.din_line, e.mem_line, e.name_line) if x is not None)
    for ln in lines:
        if CIN_RX.search(ln.text) and not re.search(r"\bDIN\b", ln.text, re.I):
            v = _label_value(ctx, sheet, CIN_RX, ln)
            if v is not None:
                sig.cin.append((v, ln))
        if FRN_RX.search(ln.text):
            v = _label_value(ctx, sheet, FRN_RX, ln)
            if v is not None:
                sig.frn.append((v, ln))
        if DATE_RX.match(ln.text):
            v = _label_value(ctx, sheet, DATE_RX, ln)
            sig.dates.append((v or "", ln))
        if PLACE_RX.match(ln.text):
            v = _label_value(ctx, sheet, PLACE_RX, ln)
            sig.places.append((v or "", ln))
    # keep only content that sits in a signature block: designations without an
    # identifier, dates and places must be near a strong anchor (DIN, membership
    # no., FRN, CIN or a block heading such as "For and on behalf of …")
    strong_rows = {x.row for e in sig.entries for x in (e.din_line, e.mem_line) if x is not None}
    strong_rows |= {ln.row for _v, ln in sig.cin + sig.frn}
    strong_rows |= {ln.row for ln in lines if BLOCK_HINT_RX.search(ln.text) and len(ln.text) < 90}

    def near(row: int, dist: int) -> bool:
        return any(abs(row - r) <= dist for r in strong_rows)

    sig.entries = [e for e in sig.entries if e.din_line is not None or e.mem_line is not None
                   or (e.role_line is not None and near(e.role_line.row, 6))]
    entry_rows = {x.row for e in sig.entries for x in (e.role_line, e.din_line, e.mem_line) if x is not None}
    strong_rows |= entry_rows
    sig.dates = [(v, ln) for v, ln in sig.dates if near(ln.row, 8)]
    sig.places = [(v, ln) for v, ln in sig.places if near(ln.row, 8)]
    # signature region: rows spanned by the block lines (±1)
    anchors = [x for e in sig.entries for x in (e.role_line, e.din_line, e.mem_line, e.name_line) if x is not None]
    anchors += [ln for _v, ln in sig.cin + sig.frn + sig.dates + sig.places]
    anchors += [ln for ln in lines if BLOCK_HINT_RX.search(ln.text) and len(ln.text) < 90]
    if sig.entries or sig.dates or sig.cin or sig.frn:
        rows = {a.row for a in anchors}
        lo, hi = min(rows) - 1, max(rows) + 1
        for ln in lines:
            if lo <= ln.row <= hi and len(ln.text) < 120:
                sig.lines.append(ln)
                sig.cells.add((ln.row, ln.col))
        for a in anchors:
            sig.cells.add((a.row, a.col))
    ctx.shared[key] = sig
    return sig


def signature_cells(ctx: CheckContext, sheet: Sheet) -> set[tuple[int, int]]:
    """Cells that belong to signature blocks (used by the column-hide safety check)."""
    sig = parse_sheet_signatures(ctx, sheet)
    return set(sig.cells) if sig.has_block else set()


# ---------------------------------------------------------------------------- word list


@lru_cache(maxsize=1)
def fixed_words() -> tuple[tuple[str, ...], frozenset[str]]:
    """(terms to check, known-correct words) from fscore/data/fixed_words.txt."""
    try:
        text = resources.files("fscore.data").joinpath("fixed_words.txt").read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError):  # pragma: no cover - packaging fallback
        text = "\n".join([*CANON_ROLES, "Chartered Accountants", "Membership", "Registration"])
    terms: list[str] = []
    known: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("known:"):
            known.add(line.split(":", 1)[1].strip().lower())
            continue
        terms.append(line)
    for t in terms:
        for w in re.findall(r"[A-Za-z][A-Za-z\-]+", t):
            known.add(w.lower())
    return tuple(terms), frozenset(known)


_SINGLE_WORDS = ("director", "partner", "proprietor", "accountants", "chartered", "secretary", "officer", "financial",
                 "executive", "managing", "chairman", "membership", "registration", "behalf")


def misspellings(text: str) -> list[tuple[str, str]]:
    """(found, expected) pairs for misspelled fixed words in a signature-block line."""
    _terms, known = fixed_words()
    out: list[tuple[str, str]] = []
    for w in re.findall(r"[A-Za-z][A-Za-z\-]{4,}", text):
        lw = w.lower()
        if lw in known:
            continue
        best = max(_SINGLE_WORDS, key=lambda x: fuzz.ratio(lw, x))
        score = fuzz.ratio(lw, best)
        if score >= 84 and abs(len(lw) - len(best)) <= 2:
            out.append((w, best.capitalize()))
    return out


# ---------------------------------------------------------------------------- master file


def load_master(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or []
    if isinstance(data, dict):
        data = data.get("signatories", [])
    return [d for d in data if isinstance(d, dict) and d.get("name")]


# ---------------------------------------------------------------------------- the check


def _ev(line: Line | None, label: str) -> Evidence:
    if line is None:
        return Evidence(label=label)
    return Evidence(label=label, ref=CellRef(sheet=line.sheet, addr=line.addr), printed=line.text)


def _statement_sheets(ctx: CheckContext) -> list[str]:
    roles = ctx.statements.roles
    return [roles[r] for r in ("balance_sheet", "profit_loss", "cash_flow", "soce") if r in roles]


@check("TXT-01")
def check_signatories(ctx: CheckContext) -> None:
    sigs: dict[str, SheetSig] = {s.name: parse_sheet_signatures(ctx, s) for s in ctx.print_sheets}
    blocks = {n: s for n, s in sigs.items() if s.has_block}
    if not blocks:
        return
    stmt_sheets = _statement_sheets(ctx)
    bs_name = ctx.statements.roles.get("balance_sheet")
    order = [s.name for s in ctx.print_sheets]
    master_sheet = bs_name if bs_name in blocks and blocks[bs_name].entries else next((n for n in order if n in blocks and blocks[n].entries), None)

    # ---- master: name -> (role, din, membership, line)
    master: dict[str, SigEntry] = {}
    if master_sheet:
        for e in blocks[master_sheet].entries:
            if e.name and e.printed and not is_placeholder(e.name):
                master.setdefault(norm_name(e.name), e)
    file_master = load_master(ctx.config.signatories)

    def master_for(name: str) -> tuple[SigEntry | None, bool]:
        """Master entry for a name; second value True when matched only by a fuzzy variant."""
        n = norm_name(name)
        if n in master:
            return master[n], False
        best, score = None, 0.0
        for k, e in master.items():
            s = fuzz.ratio(n, k)
            if s > score:
                best, score = e, s
        if best is not None and score >= 85:
            return best, True
        return None, False

    din_owner: dict[str, str] = {}
    for k, e in master.items():
        if e.din and re.fullmatch(r"\d{8}", digits(e.din)):
            d = digits(e.din)
            if d in din_owner and din_owner[d] != k:
                ctx.emit(
                    "TXT-01", sheet=e.sheet, cells=[e.din_line.addr] if e.din_line else [],
                    explanation=f"Two different signatories ({master[din_owner[d]].name} and {e.name}) show the same DIN {d} on the Balance Sheet.",
                    evidence=[_ev(master[din_owner[d]].din_line, master[din_owner[d]].name or ""), _ev(e.din_line, e.name or "")],
                    suggested_fix="Link each director's DIN to the signatory master; correct the duplicated DIN.", key=f"dup-din-master:{d}",
                )
            din_owner.setdefault(d, k)

    seen_din_people: dict[str, set[str]] = defaultdict(set)
    for sname, sig in blocks.items():
        for e in sig.entries:
            if not e.printed:
                # placeholders in hidden rows/columns would print after an unhide
                if e.din_line is not None and e.din is not None and is_placeholder(e.din):
                    ctx.emit(
                        "TXT-01", sheet=sname, cells=[e.din_line.addr], severity=Severity.LOW, certainty=Certainty.REVIEW,
                        explanation=f"Hidden signature cell holds a DIN placeholder ('{e.din_line.text}'); it would print if unhidden.",
                        evidence=[_ev(e.din_line, "DIN")], key=f"hidden-placeholder:{e.din_line.addr}",
                    )
                continue
            anchor = e.anchor
            who = e.name or (e.role or "signatory")
            # ---- name placeholder
            if e.name_line is not None and (is_placeholder(e.name or "") or re.match(r"^\[?\s*name", e.name or "", re.I)):
                ctx.emit(
                    "TXT-01", sheet=sname, cells=[e.name_line.addr], severity=Severity.HIGH,
                    explanation=f"Signatory name is a placeholder ('{e.name_line.text}').",
                    evidence=[_ev(e.name_line, "Name")], suggested_fix="Insert the signatory's name from the signatory master.",
                    key=f"name-placeholder:{e.name_line.addr}",
                )
                continue
            m_entry, fuzzy = master_for(e.name) if e.name else (None, False)
            # ---- name variants
            if e.name and m_entry is not None and fuzzy and sname != master_sheet:
                ctx.emit(
                    "TXT-01", sheet=sname, cells=[e.name_line.addr] if e.name_line else [], severity=Severity.MEDIUM, certainty=Certainty.LIKELY,
                    fix_kind=FixKind.MANUAL,
                    explanation=f"Signatory name '{e.name}' differs from '{m_entry.name}' on the {master_sheet} signature block (spelling variant).",
                    expected=m_entry.name, printed=e.name, evidence=[_ev(e.name_line, "This page"), _ev(m_entry.name_line, "Master")],
                    suggested_fix=f"Use the same spelling on every page: '{m_entry.name}'.", key=f"name-variant:{e.name_line.addr if e.name_line else ''}",
                )
            # ---- DIN
            if e.din_line is not None:
                d_raw = e.din or ""
                d = digits(d_raw)
                if is_placeholder(d_raw):
                    ctx.emit(
                        "TXT-01", sheet=sname, cells=[e.din_line.addr], severity=Severity.HIGH, certainty=Certainty.CERTAIN,
                        explanation=f"DIN of {who} is a placeholder ('{e.din_line.text}').", printed=e.din_line.text,
                        expected=m_entry.din if m_entry and m_entry.din else None, evidence=[_ev(e.din_line, "DIN")],
                        suggested_fix="Replace the placeholder with the director's 8-digit DIN from the signatory master.",
                        key=f"din-placeholder:{e.din_line.addr}",
                    )
                elif not re.fullmatch(r"\d{8}", d):
                    ctx.emit(
                        "TXT-01", sheet=sname, cells=[e.din_line.addr], severity=Severity.HIGH,
                        explanation=f"DIN of {who} '{d_raw}' is not an 8-digit number.", printed=e.din_line.text,
                        evidence=[_ev(e.din_line, "DIN")], suggested_fix="Correct the DIN (8 digits) from the signatory master.",
                        key=f"din-format:{e.din_line.addr}",
                    )
                else:
                    if e.name:
                        seen_din_people[d].add(norm_name(m_entry.name if m_entry and m_entry.name else e.name))
                    owner = din_owner.get(d)
                    if m_entry is not None and m_entry.din and re.fullmatch(r"\d{8}", digits(m_entry.din)) and digits(m_entry.din) != d:
                        belongs = f" — it is {master[owner].name}'s DIN" if owner and owner != norm_name(m_entry.name or "") else ""
                        ctx.emit(
                            "TXT-01", sheet=sname, cells=[e.din_line.addr], severity=Severity.HIGH,
                            explanation=f"{who}'s block shows DIN {d}, but the {master_sheet} block gives {digits(m_entry.din)}{belongs}.",
                            printed=d, expected=digits(m_entry.din),
                            evidence=[_ev(e.din_line, f"{who} on {sname}"), _ev(m_entry.din_line, f"{who} on {master_sheet}")],
                            suggested_fix=f"Correct the DIN to {digits(m_entry.din)} (link it to the signatory master).",
                            key=f"din-mismatch:{e.din_line.addr}",
                        )
                    elif m_entry is None and owner is not None and e.name and not same_person(e.name, master[owner].name or ""):
                        ctx.emit(
                            "TXT-01", sheet=sname, cells=[e.din_line.addr], severity=Severity.HIGH,
                            explanation=f"{who}'s block shows DIN {d}, which belongs to {master[owner].name} on the {master_sheet} block.",
                            printed=d, evidence=[_ev(e.din_line, who), _ev(master[owner].din_line, master[owner].name or "")],
                            suggested_fix="Correct the DIN from the signatory master.", key=f"din-owner:{e.din_line.addr}",
                        )
                    for fm in file_master:
                        if e.name and same_person(str(fm.get("name", "")), e.name) and fm.get("din") and digits(str(fm["din"])) != d:
                            ctx.emit(
                                "TXT-01", sheet=sname, cells=[e.din_line.addr], severity=Severity.HIGH,
                                explanation=f"{who}'s DIN {d} differs from the signatory master file ({digits(str(fm['din']))}).",
                                printed=d, expected=digits(str(fm["din"])), evidence=[_ev(e.din_line, who)],
                                suggested_fix="Use the DIN recorded in the signatory master.", key=f"din-file:{e.din_line.addr}",
                            )
            elif e.role in DIRECTOR_ROLES and e.name:
                ctx.emit(
                    "TXT-01", sheet=sname, cells=[e.role_line.addr] if e.role_line else [], severity=Severity.MEDIUM, certainty=Certainty.LIKELY,
                    explanation=f"Director {e.name}'s signature block has no DIN line.", evidence=[_ev(e.role_line, "Designation")],
                    suggested_fix="Add 'DIN: ########' under the designation.", key=f"din-missing:{e.role_line.addr if e.role_line else ''}",
                )
            # ---- membership no. (ICAI)
            if e.mem_line is not None:
                mv = e.membership or ""
                if is_placeholder(mv):
                    ctx.emit(
                        "TXT-01", sheet=sname, cells=[e.mem_line.addr], severity=Severity.HIGH,
                        explanation=f"Membership number of {who} is a placeholder ('{e.mem_line.text}').", evidence=[_ev(e.mem_line, "Membership no.")],
                        suggested_fix="Insert the partner's 6-digit ICAI membership number.", key=f"mem-placeholder:{e.mem_line.addr}",
                    )
                elif (e.role in ICAI_ROLES or e.role is None) and not re.fullmatch(r"\d{6}", digits(mv)):
                    ctx.emit(
                        "TXT-01", sheet=sname, cells=[e.mem_line.addr], severity=Severity.MEDIUM,
                        explanation=f"Membership number '{mv}' of {who} is not a 6-digit ICAI number.", evidence=[_ev(e.mem_line, "Membership no.")],
                        key=f"mem-format:{e.mem_line.addr}",
                    )
                elif m_entry is not None and m_entry.membership and digits(m_entry.membership) != digits(mv) and not is_placeholder(m_entry.membership):
                    ctx.emit(
                        "TXT-01", sheet=sname, cells=[e.mem_line.addr], severity=Severity.HIGH,
                        explanation=f"{who}'s membership no. {digits(mv)} differs from {digits(m_entry.membership)} on the {master_sheet} block.",
                        printed=digits(mv), expected=digits(m_entry.membership),
                        evidence=[_ev(e.mem_line, sname), _ev(m_entry.mem_line, master_sheet or "")], key=f"mem-mismatch:{e.mem_line.addr}",
                    )
            # ---- role differs from master
            if m_entry is not None and e.role and m_entry.role and e.role != m_entry.role and not fuzzy:
                ctx.emit(
                    "TXT-01", sheet=sname, cells=[e.role_line.addr] if e.role_line else [], severity=Severity.LOW, certainty=Certainty.REVIEW,
                    fix_kind=FixKind.MANUAL,
                    explanation=f"{who} is shown as '{e.role}' here but as '{m_entry.role}' on the {master_sheet} block.",
                    evidence=[_ev(e.role_line, sname), _ev(m_entry.role_line, master_sheet or "")], key=f"role:{e.role_line.addr if e.role_line else ''}",
                )
            _ = anchor
    # ---- two different people sharing a DIN across pages
    for d, people in seen_din_people.items():
        if len(people) <= 1:
            continue
        names = sorted(people)
        for sname, sig in blocks.items():
            for e in sig.entries:
                if e.printed and e.din_line is not None and digits(e.din or "") == d and e.name and din_owner.get(d) not in (None, norm_name(e.name)):
                    ctx.emit(
                        "TXT-01", sheet=sname, cells=[e.din_line.addr], severity=Severity.HIGH,
                        explanation=f"DIN {d} is shown for different people ({', '.join(names)}); a DIN identifies one person.",
                        evidence=[_ev(e.din_line, e.name)], key=f"din-owner:{e.din_line.addr}",
                    )

    # ---- CIN / FRN consistency and format
    def ident_check(attr: str, label: str, fmt: re.Pattern[str], fmt_hint: str) -> None:
        values: list[tuple[str, Line, str]] = []
        for sname, sig in blocks.items():
            for v, ln in getattr(sig, attr):
                if not ln.printed:
                    continue
                if is_placeholder(v):
                    ctx.emit(
                        "TXT-01", sheet=sname, cells=[ln.addr], severity=Severity.HIGH,
                        explanation=f"{label} is a placeholder ('{ln.text}').", evidence=[_ev(ln, label)], key=f"{attr}-placeholder:{ln.addr}",
                    )
                    continue
                norm = re.sub(r"\s+", "", v).upper()
                values.append((norm, ln, sname))
                if not fmt.fullmatch(norm):
                    ctx.emit(
                        "TXT-01", sheet=sname, cells=[ln.addr], severity=Severity.MEDIUM,
                        explanation=f"{label} '{v}' does not have the expected format ({fmt_hint}).", evidence=[_ev(ln, label)],
                        key=f"{attr}-format:{ln.addr}",
                    )
        if len({v for v, _l, _s in values}) > 1:
            ref_val = next((v for v, _l, s in values if s == master_sheet), None) or Counter(v for v, _l, _s in values).most_common(1)[0][0]
            for v, ln, sname in values:
                if v != ref_val:
                    ctx.emit(
                        "TXT-01", sheet=sname, cells=[ln.addr], severity=Severity.HIGH,
                        explanation=f"{label} '{v}' on {sname} differs from '{ref_val}' shown elsewhere; it must be identical on every page.",
                        printed=v, expected=ref_val, evidence=[_ev(ln, label)], key=f"{attr}-differs:{ln.addr}",
                    )

    ident_check("cin", "CIN", CIN_FORMAT, "21 characters, e.g. U72900KA2015PTC012345")
    ident_check("frn", "Firm Registration No.", FRN_FORMAT, "6 digits and a letter, e.g. 123456W")

    # ---- signing date and place
    year_end = ctx.config.year_end
    window = timedelta(days=ctx.config.signing_window_days)
    parsed_dates: list[tuple[date, Line, str]] = []
    for sname, sig in blocks.items():
        for v, ln in sig.dates:
            if not ln.printed:
                continue
            if is_placeholder(v):
                ctx.emit(
                    "TXT-01", sheet=sname, cells=[ln.addr], severity=Severity.HIGH,
                    explanation=f"Signing date is blank or a placeholder ('{ln.text}').", evidence=[_ev(ln, "Date")],
                    suggested_fix="Insert the signing date (identical on every statement).", key=f"date-placeholder:{ln.addr}",
                )
                continue
            ds = find_dates(v)
            if not ds:
                ctx.emit(
                    "TXT-01", sheet=sname, cells=[ln.addr], severity=Severity.MEDIUM, certainty=Certainty.REVIEW,
                    explanation=f"Signing date '{v}' could not be read as a date.", evidence=[_ev(ln, "Date")], key=f"date-unreadable:{ln.addr}",
                )
                continue
            sd, bad, _txt = ds[0]
            parsed_dates.append((sd, ln, sname))
            if bad:
                ctx.emit(
                    "TXT-01", sheet=sname, cells=[ln.addr], severity=Severity.HIGH,
                    explanation=f"Signing date '{v}' is not a valid calendar date.", evidence=[_ev(ln, "Date")], key=f"date-malformed:{ln.addr}",
                )
            elif sd <= year_end:
                ctx.emit(
                    "TXT-01", sheet=sname, cells=[ln.addr], severity=Severity.HIGH,
                    explanation=f"Signing date {sd:%d-%b-%Y} is not after the period end {year_end:%d-%b-%Y}.", evidence=[_ev(ln, "Date")],
                    key=f"date-before:{ln.addr}",
                )
            elif sd > year_end + window:
                ctx.emit(
                    "TXT-01", sheet=sname, cells=[ln.addr], severity=Severity.MEDIUM, certainty=Certainty.LIKELY,
                    explanation=f"Signing date {sd:%d-%b-%Y} is more than {ctx.config.signing_window_days} days after the period end.",
                    evidence=[_ev(ln, "Date")], key=f"date-late:{ln.addr}",
                )
    if parsed_dates:
        ref = next((x for x, _l, s in parsed_dates if s == master_sheet), None) or Counter(x for x, _l, _s in parsed_dates).most_common(1)[0][0]
        for sd, ln, sname in parsed_dates:
            if sd != ref:
                ctx.emit(
                    "TXT-01", sheet=sname, cells=[ln.addr], severity=Severity.HIGH,
                    explanation=f"Signing date {sd:%d-%b-%Y} on {sname} differs from {ref:%d-%b-%Y} on the other statements.",
                    printed=f"{sd:%d-%b-%Y}", expected=f"{ref:%d-%b-%Y}", evidence=[_ev(ln, "Date")], key=f"date-differs:{ln.addr}",
                )
    any_dated = any(sig.dates for sig in blocks.values())
    for sname in stmt_sheets:
        ssig = sigs.get(sname)
        if ssig is None:
            continue
        if not ssig.has_block:
            if len(blocks) >= 2:
                ctx.emit(
                    "TXT-01", sheet=sname, severity=Severity.MEDIUM, certainty=Certainty.LIKELY, fix_kind=FixKind.MANUAL,
                    explanation=f"No signature block found on {sname} although other statements carry one.", key=f"no-block:{sname}",
                )
            continue
        if any_dated and not [1 for _v, ln in ssig.dates if ln.printed]:
            ctx.emit(
                "TXT-01", sheet=sname, severity=Severity.HIGH,
                explanation=f"The signature block on {sname} has no signing date.", key=f"no-date:{sname}",
                suggested_fix="Add 'Date:' with the signing date (identical on every statement).",
            )
        printed_places = [(v, ln) for v, ln in ssig.places if ln.printed]
        if ssig.dates and not printed_places:
            ctx.emit(
                "TXT-01", sheet=sname, severity=Severity.MEDIUM, certainty=Certainty.LIKELY,
                explanation=f"The signature block on {sname} has no place of signing.", key=f"no-place:{sname}",
            )
        for v, ln in printed_places:
            if is_placeholder(v):
                ctx.emit(
                    "TXT-01", sheet=sname, cells=[ln.addr], severity=Severity.MEDIUM,
                    explanation=f"Place of signing is blank or a placeholder ('{ln.text}').", evidence=[_ev(ln, "Place")], key=f"place-placeholder:{ln.addr}",
                )

    # ---- spelling of fixed words in signature blocks
    for sname, sig in blocks.items():
        for ln in sig.lines:
            if not ln.printed:
                continue
            for found, expected in misspellings(ln.text):
                ctx.emit(
                    "TXT-01", sheet=sname, cells=[ln.addr], severity=Severity.LOW, certainty=Certainty.LIKELY, fix_kind=FixKind.APPROVAL,
                    explanation=f"'{found}' looks like a misspelling of '{expected}' in the signature block.",
                    printed=ln.text, expected=expected, evidence=[_ev(ln, "Signature block")],
                    suggested_fix=f"Correct the spelling to '{expected}'.", key=f"spelling:{ln.addr}:{found.lower()}",
                )
