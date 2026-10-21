"""PDF reconciliation (02 §6.7): PDF-01 staleness, PDF-02 text, PDF-03 invisible values,
PDF-04 PDF-only casting.

Pages are mapped to print sheets in print order (monotonic dynamic programme
over number-overlap and heading scores). Every printed number of a sheet must
appear on its mapped page(s): matching is a multiset comparison of normalised
printed numbers, so grouping (Indian / western), brackets vs minus and
currency symbols do not matter. Text that FS-Platform overlays on a page
(watermarks, run-ID footers) is ignored by the reader.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from dateutil import parser as dateparser

from fscore.checks.base import CheckContext, is_total_caption
from fscore.config import EngagementConfig
from fscore.engine.display import numeric_signature
from fscore.io.pdf_reader import ERROR_TOKENS, PdfDocument, PdfPage, error_tokens, fonts_embedded, read_pdf
from fscore.io.xlsx_reader import Sheet, Workbook
from fscore.models import (
    CellRef,
    Certainty,
    Evidence,
    Finding,
    FixKind,
    PdfMiss,
    PdfPageMap,
    PdfReconResult,
    Severity,
)
from fscore.registry import check, get_rule

ROLE_WORDS = re.compile(
    r"^(managing director|whole[- ]time director|director|partner|proprietor|chief financial officer|"
    r"chief executive officer|company secretary|ceo|cfo|chairman|chairperson|manager)\b", re.I
)
_DIN = re.compile(r"\bDIN\s*(?:No\.?)?\s*[:\-]?\s*([0-9X]{4,10})\b", re.I)
_CIN = re.compile(r"\b[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b")
_MEMB = re.compile(r"Membership\s*(?:No\.?|Number)\s*[:\-]?\s*([0-9X]{4,7})", re.I)
_FRN = re.compile(r"(?:FRN|Firm(?:'s)?\s+Registration\s+(?:No\.?|Number))\s*[:\-]?\s*([0-9X]{6}[A-Z]?)", re.I)
_DATE_LINE = re.compile(r"^\s*Date\s*[:\-]\s*(.+?)\s*$", re.I)
_PLACE_LINE = re.compile(r"^\s*Place\s*[:\-]\s*(.+?)\s*$", re.I)
_PERIOD_DATE = re.compile(
    r"(?:as\s+at|as\s+on|ended|ending|year\s+ended|period\s+ended|quarter\s+ended)\s*,?\s*"
    r"((?:\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9},?\s+\d{4})|(?:[A-Za-z]{3,9}\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})|(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4}))",
    re.I,
)
STALE_MESSAGE = "PDF appears to be from an older version of the workbook"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


@dataclass
class Expected:
    sheet: str
    row: int
    col: int
    addr: str
    printed: str
    signature: str
    caption: str
    invisible: bool = False


@dataclass
class IdentityItem:
    sheet: str
    addr: str
    kind: str  # din, cin, membership, frn, date, place, name
    value: str
    text: str  # the full cell text


@dataclass
class ReconState:
    """Everything reconciliation learnt; reused by the registered check and by render verification."""

    result: PdfReconResult
    doc: PdfDocument
    expected: dict[str, list[Expected]] = field(default_factory=dict)
    invisible: dict[str, list[Expected]] = field(default_factory=dict)
    identity: list[IdentityItem] = field(default_factory=list)
    sheet_pages: dict[str, list[int]] = field(default_factory=dict)
    identity_mismatches: list[tuple[IdentityItem, list[int], list[str]]] = field(default_factory=list)
    invisible_found: list[tuple[Expected, str]] = field(default_factory=list)  # (cell, "invisible"|"absent")
    print_sheets: list[str] = field(default_factory=list)


# ------------------------------------------------------------------------------- expected values


def collect_expected(ctx: CheckContext) -> tuple[dict[str, list[Expected]], dict[str, list[Expected]]]:
    visible: dict[str, list[Expected]] = {}
    invisible: dict[str, list[Expected]] = {}
    for sheet in ctx.print_sheets:
        vis: list[Expected] = []
        inv: list[Expected] = []
        for c in ctx.iter_printed_numbers(sheet):
            r = ctx.wb.render_cell(c)
            text = r.clean
            sig = numeric_signature(text)
            if sig is None:
                continue
            e = Expected(sheet.name, c.row, c.col, c.addr, text, sig, ctx.caption(sheet, c.row, before_col=c.col), False)
            if sheet.font_invisible(c):
                e.invisible = True
                inv.append(e)
            else:
                vis.append(e)
        visible[sheet.name] = vis
        invisible[sheet.name] = inv
    return visible, invisible


def collect_identity(ctx: CheckContext) -> list[IdentityItem]:
    out: list[IdentityItem] = []
    for sheet in ctx.print_sheets:
        texts = {(c.row, c.col): str(c.value).strip() for c in ctx.iter_printed_text(sheet) if not sheet.font_invisible(c)}
        for (r, col), t in texts.items():
            addr = sheet.cell(r, col).addr  # type: ignore[union-attr]
            for m in _DIN.finditer(t):
                out.append(IdentityItem(sheet.name, addr, "din", m.group(1), t))
            for m in _CIN.finditer(t):
                out.append(IdentityItem(sheet.name, addr, "cin", m.group(0), t))
            for m in _MEMB.finditer(t):
                out.append(IdentityItem(sheet.name, addr, "membership", m.group(1), t))
            for m in _FRN.finditer(t):
                out.append(IdentityItem(sheet.name, addr, "frn", m.group(1), t))
            md = _DATE_LINE.match(t)
            if md:
                out.append(IdentityItem(sheet.name, addr, "date", md.group(1), t))
            mp = _PLACE_LINE.match(t)
            if mp:
                out.append(IdentityItem(sheet.name, addr, "place", mp.group(1), t))
            if ROLE_WORDS.match(t):
                for up in (1, 2):
                    above = texts.get((r - up, col))
                    if above and not ROLE_WORDS.match(above) and len(above) <= 60 and not re.search(r"\d", above):
                        if not re.search(r"(for and on behalf|chartered accountants|limited|private|llp|\bco\.)", above, re.I):
                            out.append(IdentityItem(sheet.name, sheet.cell(r - up, col).addr, "name", above, above))  # type: ignore[union-attr]
                        break
    return out


# ------------------------------------------------------------------------------- page mapping


def _sheet_headings(ctx: CheckContext, sheet: Sheet) -> list[str]:
    """First few visible text lines of the print area: titles used to recognise its pages."""
    heads: list[str] = []
    for area in sheet.print_bounds[:1]:
        for r in range(area.r1, min(area.r2, area.r1 + 12) + 1):
            if sheet.row_hidden(r):
                continue
            for c in range(area.c1, min(area.c2, area.c1 + 3) + 1):
                cell = sheet.cell(r, c)
                if cell is not None and isinstance(cell.value, str) and len(cell.value.strip()) >= 6 and not sheet.font_invisible(cell):
                    heads.append(_norm(cell.value))
    return heads[:6]


def map_pages(ctx: CheckContext, doc: PdfDocument, expected: dict[str, list[Expected]]) -> dict[int, str | None]:
    sheets = list(ctx.print_sheets)
    names = [s.name for s in sheets]
    if not names or not doc.pages:
        return {p.number: None for p in doc.pages}
    exp_counts = {n: Counter(e.signature for e in expected.get(n, [])) for n in names}
    heads = {s.name: _sheet_headings(ctx, s) for s in sheets}
    vocab = _distinctive_words(ctx, sheets)
    scores: list[list[float]] = []
    for page in doc.pages:
        pc = Counter(t.signature for t in page.numbers if not t.invisible)
        ptext = _norm(page.visible_text)
        pwords = set(_WORD.findall(ptext))
        word_hits = {n: len(vocab[n] & pwords) for n in names}
        best_words = max(word_hits.values(), default=0)
        row: list[float] = []
        total = max(1, sum(pc.values()))
        for n in names:
            inter = sum(min(v, exp_counts[n].get(k, 0)) for k, v in pc.items())
            s = inter / total
            hs = heads[n]
            if hs:
                hit = sum(1 for h in hs if h and h in ptext)
                s += 0.6 * hit / len(hs)
            if best_words >= 3:
                # words found only in this sheet's printed text decide text-only pages (policies, narrative notes)
                s += 0.8 * word_hits[n] / best_words
            row.append(s)
        scores.append(row)
    return _monotone_assign(scores, names, [p.number for p in doc.pages])


_WORD = re.compile(r"[a-z]{4,}")


def _distinctive_words(ctx: CheckContext, sheets: list[Sheet]) -> dict[str, set[str]]:
    """Per sheet, the words of its printed text that occur in at most two print sheets."""
    words: dict[str, set[str]] = {}
    for sh in sheets:
        text = " ".join(str(c.value) for c in ctx.iter_printed_text(sh) if not sh.font_invisible(c))
        words[sh.name] = set(_WORD.findall(_norm(text)))
    df: Counter[str] = Counter()
    for ws in words.values():
        df.update(ws)
    return {n: {w for w in ws if df[w] <= 2} for n, ws in words.items()}


def _monotone_assign(scores: list[list[float]], names: list[str], page_numbers: list[int]) -> dict[int, str | None]:
    """Assign pages to sheets in print order (sheet index never decreases).

    State = index of the sheet most recently assigned (-1 before the first).
    A page is either assigned to a sheet j >= state, or left unassigned
    (carrying the state, small penalty) — e.g. a cover or report page.
    """
    n_s = len(names)
    neg = float("-inf")
    size = n_s + 1  # state index + 1 (0 = nothing assigned yet)
    dp = [0.0] + [neg] * n_s
    choices: list[list[tuple[str, int]]] = []
    for i in range(len(page_numbers)):
        new = [neg] * size
        choice: list[tuple[str, int]] = [("carry", 0)] * size
        prefix_best = neg
        prefix_arg = -1
        for st in range(size):
            # carry (unassigned page)
            if dp[st] != neg:
                v = dp[st] - 0.01
                if v > new[st]:
                    new[st], choice[st] = v, ("carry", st)
            if st >= 1:
                j = st - 1
                here = scores[i][j]
                cand, arg = prefix_best, prefix_arg
                if dp[st] != neg and dp[st] + 0.05 >= cand:
                    cand, arg = dp[st] + 0.05, st
                if cand != neg:
                    v = cand + here
                    if v > new[st]:
                        new[st], choice[st] = v, ("assign", arg)
            if dp[st] != neg and dp[st] > prefix_best:
                prefix_best, prefix_arg = dp[st], st
        dp = new
        choices.append(choice)
    st = max(range(size), key=lambda k: dp[k])
    out: dict[int, str | None] = {}
    for i in range(len(page_numbers) - 1, -1, -1):
        kind, prev = choices[i][st]
        out[page_numbers[i]] = names[st - 1] if kind == "assign" else None
        st = prev
    return out


# ------------------------------------------------------------------------------- reconciliation core


def reconcile_ctx(ctx: CheckContext, pdf_path: Path, *, doc: PdfDocument | None = None) -> ReconState:
    doc = doc or read_pdf(pdf_path)
    expected, invisible = collect_expected(ctx)
    identity = collect_identity(ctx)
    assign = map_pages(ctx, doc, expected)
    sheet_pages: dict[str, list[int]] = defaultdict(list)
    for pno, sh in sorted(assign.items()):
        if sh is not None:
            sheet_pages[sh].append(pno)
    result = PdfReconResult(pdf_sha256=doc.sha256, pages=len(doc.pages))
    for pno in sorted(assign):
        sh = assign[pno]
        page = doc.page(pno)
        if sh is None:
            score = 0.0
        else:
            pc = Counter(t.signature for t in page.numbers if not t.invisible)
            ec = Counter(e.signature for e in expected.get(sh, []))
            score = round(sum(min(v, ec.get(k, 0)) for k, v in pc.items()) / max(1, sum(pc.values())), 3)
        result.page_map.append(PdfPageMap(page=pno, sheet=sh, score=score))
    misses: list[PdfMiss] = []
    checked = 0
    reprint: set[int] = set()
    for sheet in ctx.print_sheets:
        exp = expected.get(sheet.name, [])
        pages = sheet_pages.get(sheet.name, [])
        pool: Counter[str] = Counter()
        for pno in pages:
            pool.update(t.signature for t in doc.page(pno).numbers if not t.invisible)
        for e in exp:
            checked += 1
            if pool[e.signature] > 0:
                pool[e.signature] -= 1
                continue
            misses.append(PdfMiss(sheet=e.sheet, cell=e.addr, printed=e.printed, caption=e.caption or None, pages=pages))
            reprint.update(pages)
    result.values_checked = checked
    result.misses = misses
    result.stale = bool(misses)
    result.reprint_pages = sorted(reprint)
    result.error_tokens = error_tokens(doc)
    result.blank_pages = [p.number for p in doc.pages if p.is_blank]
    state = ReconState(result=result, doc=doc, expected=expected, invisible=invisible, identity=identity,
                       sheet_pages=dict(sheet_pages), print_sheets=[s.name for s in ctx.print_sheets])
    # PDF-02 identity text
    for item in identity:
        pages = sheet_pages.get(item.sheet, [])
        if not pages:
            continue
        text = " ".join(_norm(doc.page(p).visible_text) for p in pages)
        needle = _norm(item.value)
        if needle and needle in text:
            continue
        found: list[str] = []
        if item.kind == "din":
            found = sorted({m.group(1) for p in pages for m in _DIN.finditer(doc.page(p).visible_text)} - {i.value for i in identity if i.kind == "din"})
        elif item.kind == "cin":
            found = sorted({m.group(0) for p in pages for m in _CIN.finditer(doc.page(p).visible_text)} - {item.value})
        state.identity_mismatches.append((item, pages, found))
        result.text_mismatches.append(f"{item.kind.upper()} '{item.value}' ({item.sheet}!{item.addr}) not found on page(s) {', '.join(map(str, pages))}" + (f"; PDF shows {', '.join(found)}" if found else ""))
    # placeholders printed in signature blocks
    for page in doc.pages:
        for m in re.finditer(r"\b(DIN|Date|Membership No\.?)\s*[:\-]\s*(X{2,}|_{3,})", page.visible_text, re.I):
            result.text_mismatches.append(f"placeholder '{m.group(0)}' printed on page {page.number}")
    # PDF-03 invisible values
    for sheet_name, inv in invisible.items():
        pages = sheet_pages.get(sheet_name, [])
        vis_pool: Counter[str] = Counter()
        inv_pool: Counter[str] = Counter()
        for pno in pages:
            for t in doc.page(pno).numbers:
                (inv_pool if t.invisible else vis_pool)[t.signature] += 1
        for e in inv:
            if vis_pool[e.signature] > 0:
                vis_pool[e.signature] -= 1
                continue  # printed visibly after all (font colour differs from the workbook)
            state.invisible_found.append((e, "invisible" if inv_pool[e.signature] > 0 else "absent"))
    return state


def reconcile(path: Path, pdf_path: Path, config: EngagementConfig) -> PdfReconResult:
    """Public entry point used by fscore.api.reconcile_pdf."""
    with Workbook.open(path) as wb:
        ctx = CheckContext(wb, config, pdf_path=pdf_path)
        return reconcile_ctx(ctx, Path(pdf_path)).result


def reprint_list(result: PdfReconResult, changed: list[tuple[str, str]]) -> list[int]:
    """Pages that will print differently after a fix: pages mapped to the sheets of changed cells."""
    sheets = {s for s, _a in changed}
    return sorted({pm.page for pm in result.page_map if pm.sheet in sheets})


# ------------------------------------------------------------------------------- verification helpers (render)


def allowed_period_dates(config: EngagementConfig) -> set[date]:
    """Period ends in scope, their first days and the preceding period ends."""
    from dateutil.relativedelta import relativedelta

    allowed: set[date] = set()
    for p in config.scope():
        allowed.add(p.end)
        start = p.end + timedelta(days=1) - relativedelta(months=p.months)
        allowed.add(start)
        allowed.add(start - timedelta(days=1))
    return allowed


def period_problems(doc: PdfDocument, config: EngagementConfig) -> list[str]:
    """Headings that quote a date outside the configured reporting periods."""
    allowed = allowed_period_dates(config)
    out: list[str] = []
    for page in doc.pages:
        for m in _PERIOD_DATE.finditer(page.visible_text.replace("\n", " ")):
            raw = m.group(1)
            try:
                d = dateparser.parse(raw, dayfirst=not re.match(r"[A-Za-z]", raw), fuzzy=True).date()
            except (ValueError, OverflowError):
                out.append(f"malformed date '{raw}' on page {page.number}")
                continue
            if d not in allowed:
                out.append(f"out-of-scope period '{raw.strip()}' on page {page.number}")
    return sorted(set(out))


def rupee_problems(ctx: CheckContext, state: ReconState) -> list[str]:
    out: list[str] = []
    for sheet in ctx.print_sheets:
        has_rupee = any("₹" in str(c.value) for c in ctx.iter_printed_text(sheet) if not sheet.font_invisible(c))
        pages = state.sheet_pages.get(sheet.name, [])
        if not has_rupee or not pages:
            continue
        texts = [state.doc.page(pno).visible_text for pno in pages]
        # the unit line usually prints once per sheet: the glyph must render somewhere on the sheet's pages,
        # and never be replaced by '?' in front of the unit word
        if not any("₹" in t for t in texts):
            out.append(f"'₹' does not render on the pages of {sheet.name} ({', '.join(map(str, pages))})")
        for pno, t in zip(pages, texts, strict=True):
            if re.search(r"\?\s*(lakhs?|crores?|million|thousands?)\b", t, re.I):
                out.append(f"'₹' printed as '?' on page {pno} ({sheet.name})")
    for page in state.doc.pages:
        if "�" in page.visible_text:
            out.append(f"replacement characters on page {page.number}")
    return out


def cutoff_problems(doc: PdfDocument) -> list[str]:
    out = []
    for p in doc.pages:
        for w in p.words:
            if w.invisible:
                continue
            if w.x1 > p.width - 1.0 or w.x0 < 1.0 or w.y1 > p.height - 1.0 or w.y0 < 1.0:
                out.append(f"text cut off at the page edge on page {p.number} ('{w.text[:20]}')")
                break
    return out


def verify(ctx: CheckContext, pdf_path: Path) -> tuple[ReconState, list[str]]:
    """Post-conversion verification (02 §10A). Returns the state and a list of problems."""
    state = reconcile_ctx(ctx, pdf_path)
    r = state.result
    problems: list[str] = []
    if r.misses:
        problems.append(f"{len(r.misses)} printed value(s) missing from the PDF")
    content_sheets = [s for s in state.print_sheets if state.expected.get(s) or any(True for _ in ctx.iter_printed_text(ctx.wb.sheet(s)))]
    unmapped = [s for s in content_sheets if not state.sheet_pages.get(s)]
    if unmapped:
        problems.append("print sheet(s) without a page: " + ", ".join(unmapped))
    if len(state.doc.pages) < len(content_sheets):
        problems.append(f"page count {len(state.doc.pages)} is less than the {len(content_sheets)} print sheets")
    if r.blank_pages:
        problems.append("blank page(s): " + ", ".join(map(str, r.blank_pages)))
    if r.error_tokens:
        problems.append("error values printed: " + "; ".join(r.error_tokens[:5]))
    missing_fonts = fonts_embedded(state.doc)
    if missing_fonts:
        problems.append("fonts not embedded: " + ", ".join(missing_fonts[:5]))
    problems.extend(rupee_problems(ctx, state))
    problems.extend(cutoff_problems(state.doc))
    periods = period_problems(state.doc, ctx.config)
    r.text_mismatches.extend(periods)
    problems.extend(periods)
    return state, problems


# ------------------------------------------------------------------------------- registered check


@check("PDF-01", "PDF-02", "PDF-03", "PDF-04", order=900, needs_pdf=True)
def pdf_checks(ctx: CheckContext) -> None:
    """Workbook vs printed PDF (runs only when a PDF is supplied)."""
    if ctx.pdf_path is None:
        return
    pdf_path = Path(ctx.pdf_path)
    if not pdf_path.exists():
        ctx.warnings.append("PDF not found; PDF reconciliation skipped")
        return
    try:
        state = reconcile_ctx(ctx, pdf_path)
    except Exception as exc:
        ctx.warnings.append(f"PDF could not be read ({type(exc).__name__}); PDF reconciliation skipped")
        return
    ctx.shared["pdf_recon"] = state.result
    r = state.result
    by_sheet: dict[str, list[PdfMiss]] = defaultdict(list)
    for m in r.misses:
        by_sheet[m.sheet].append(m)
    total_values = max(1, r.values_checked)
    if r.misses:
        share = len(r.misses) / total_values
        ctx.emit(
            "PDF-01",
            key="overall",
            explanation=(
                f"{STALE_MESSAGE}: {len(r.misses)} of {r.values_checked} printed values are not on the PDF "
                f"({share:.0%}). Re-print page(s): {', '.join(map(str, r.reprint_pages)) or 'all'}."
            ),
            printed=f"{len(r.misses)} missing",
            suggested_fix="Re-render the PDF from the current (corrected) workbook and re-run the reconciliation.",
            tags=["stale-pdf"],
        )
    for sheet_name, items in by_sheet.items():
        pages = items[0].pages
        ev = [
            Evidence(label=m.caption or m.cell, ref=CellRef(sheet=sheet_name, addr=m.cell), printed=m.printed, note=f"not on page(s) {', '.join(map(str, pages)) or '—'}")
            for m in items[:25]
        ]
        ctx.emit(
            "PDF-01",
            sheet=sheet_name,
            cells=[m.cell for m in items[:200]],
            key=f"sheet:{sheet_name}",
            explanation=(
                f"{len(items)} printed value(s) on '{sheet_name}' do not appear on "
                + (f"PDF page(s) {', '.join(map(str, pages))}" if pages else "any PDF page (sheet missing from the PDF)")
                + "."
            ),
            printed=items[0].printed,
            evidence=ev,
            suggested_fix="Re-print these pages from the current workbook.",
        )
    for tok in r.error_tokens:
        ctx.emit("PDF-02", key=f"err:{tok}", explanation=f"Excel error value printed in the PDF: {tok}.",
                 suggested_fix="Fix the formula in the workbook and re-print.", severity=Severity.HIGH)
    for item, pages, found in state.identity_mismatches:
        ctx.emit(
            "PDF-02",
            sheet=item.sheet,
            cells=[item.addr],
            key=f"id:{item.kind}:{item.addr}",
            caption=item.text,
            printed=", ".join(found) if found else None,
            expected=item.value,
            explanation=(
                f"{item.kind.upper()} '{item.value}' in the workbook ({item.sheet}!{item.addr}) is not printed on page(s) "
                f"{', '.join(map(str, pages))}" + (f"; the PDF shows {', '.join(found)} instead." if found else ".")
            ),
            suggested_fix="Re-print the signature pages from the current workbook after confirming the signatory master.",
        )
    for msg in [m for m in r.text_mismatches if m.startswith("placeholder")]:
        ctx.emit("PDF-02", key=f"ph:{msg}", explanation=f"Signature-block placeholder printed: {msg}.",
                 suggested_fix="Replace placeholders with the signatory master values and re-print.")
    for e, how in state.invisible_found:
        ctx.emit(
            "PDF-03",
            sheet=e.sheet,
            cells=[e.addr],
            key=f"inv:{e.addr}",
            caption=e.caption or None,
            printed=e.printed,
            explanation=(
                f"Value {e.printed} at {e.sheet}!{e.addr} is inside the print area but "
                + ("is drawn in an invisible (white) font on the PDF" if how == "invisible" else "does not appear on the PDF")
                + "; it would print if the formatting changed (corroborates HID-02)."
            ),
            suggested_fix="Move working figures outside the print area or clear them.",
        )


# ------------------------------------------------------------------------------- PDF-only mode (PDF-04)


@dataclass
class _Row:
    page: int
    y: float
    caption: str
    amounts: list[tuple[float, Decimal, str]]  # (x1, value, text)
    bold: bool


_HEAD_NUM = re.compile(r"^\(?[ivxlcdm]+\)?$|^\(?[a-z]\)$", re.I)


def _is_amount_text(t: str) -> bool:
    """Printed amounts carry a decimal point, a grouping comma, brackets or a dash.

    Plain integers (note references, years, DINs, page numbers) are not amounts.
    """
    t = t.strip()
    return t in ("-", "–", "—") or "." in t or "," in t or (t.startswith("(") and t.endswith(")"))


def _rows_of(page: PdfPage) -> list[_Row]:
    tok_at = {(round(t.x0, 1), round(t.y, 1)): t for t in page.numbers}
    out: list[_Row] = []
    for ln in page.lines:
        words = [w for w in ln.words if not w.invisible]
        if not words:
            continue
        amounts: list[tuple[float, Decimal, str]] = []
        caption_words: list[str] = []
        for w in words:
            tok = tok_at.get((round(w.x0, 1), round(w.yc, 1)))
            in_amount_zone = w.x0 > page.width * (0.35 if caption_words else 0.45)
            if tok is not None and in_amount_zone and _is_amount_text(tok.text):
                amounts.append((w.x1, tok.value, tok.text))
            elif not amounts:
                caption_words.append(w.text)
        out.append(_Row(page.number, ln.y, " ".join(caption_words), amounts, any(w.bold for w in words)))
    return out


def _column_centres(rows: list[_Row], tol: float = 6.0) -> list[float]:
    """Cluster the right edges of printed amounts (right-aligned columns) across all pages."""
    xs = sorted(x for r in rows for x, _v, _t in r.amounts)
    groups: list[list[float]] = []
    for x in xs:
        if groups and x - groups[-1][-1] <= tol:
            groups[-1].append(x)
        else:
            groups.append([x])
    return [sum(g) / len(g) for g in groups]


def cast_pdf_tables(doc: PdfDocument) -> list[dict[str, object]]:
    """PDF-04: rebuild tables from word positions and cast every 'Total' row on printed values.

    A block runs from the previous total (or the start of a page that opens
    with a heading) to the next total row. A table continuing from the previous
    page is also tried with the carried-over lines, and grand totals are tried
    against the subtotals above them.
    """
    problems: list[dict[str, object]] = []
    pages_rows = [(page, _rows_of(page)) for page in doc.pages]
    centres = _column_centres([r for _p, rows in pages_rows for r in rows])
    if not centres:
        return problems

    def col(x: float) -> int | None:
        i = min(range(len(centres)), key=lambda k: abs(centres[k] - x))
        return i if abs(centres[i] - x) <= 8 else None

    block: dict[int, list[Decimal]] = defaultdict(list)
    carry: dict[int, list[Decimal]] = defaultdict(list)
    subtotals: dict[int, list[Decimal]] = defaultdict(list)
    for page, rows in pages_rows:
        if rows and not rows[0].amounts:
            # a page that opens with a heading: keep the previous lines in case the table continues
            for ck, cv in block.items():
                carry[ck].extend(cv)
            block = defaultdict(list)
        for row in rows:
            if not row.amounts:
                continue
            vals: dict[int, tuple[Decimal, str]] = {}
            for x, amount, text in row.amounts:
                ci = col(x)
                if ci is not None:
                    vals[ci] = (amount, text)
            if is_total_caption(row.caption):
                for k, (v, t) in vals.items():
                    lines = block.get(k, [])
                    if len(lines) + len(carry.get(k, [])) < 2:
                        continue
                    s_lines = sum(lines, Decimal(0))
                    candidates = [s_lines, s_lines + sum(carry.get(k, []), Decimal(0))]
                    subs = subtotals.get(k, [])
                    if len(subs) >= 2:
                        candidates.append(sum(subs, Decimal(0)))
                    if v in candidates:
                        continue
                    if not lines:
                        continue
                    problems.append({
                        "page": page.number, "caption": row.caption, "printed": t, "sum": str(s_lines),
                        "difference": str(v - s_lines), "column": k,
                    })
                for k, (v, _t) in vals.items():
                    subtotals[k].append(v)
                block = defaultdict(list)
                carry = defaultdict(list)
                continue
            for k, (v, _t) in vals.items():
                block[k].append(v)
    return problems


def _finding(rule_id: str, **kw: object) -> Finding:
    info = get_rule(rule_id)
    data: dict[str, object] = {
        "rule_id": rule_id,
        "rule_version": info.version,
        "family": info.family,
        "severity": info.severity,
        "certainty": info.certainty,
        "category": info.title,
        "fix_kind": info.fix_kind,
    }
    data.update(kw)
    f = Finding.model_validate(data)
    f.fingerprint = f.compute_fingerprint()
    return f


def check_pdf_only(pdf_path: Path, config: EngagementConfig | None = None) -> tuple[list[Finding], PdfReconResult]:
    """PDF-only mode: no workbook. Casting on the printed tables, error tokens; lower certainty, no fixes."""
    doc = read_pdf(pdf_path)
    findings: list[Finding] = []
    result = PdfReconResult(pdf_sha256=doc.sha256, pages=len(doc.pages))
    result.error_tokens = error_tokens(doc)
    result.blank_pages = [p.number for p in doc.pages if p.is_blank]
    for i, prob in enumerate(cast_pdf_tables(doc)):
        diff = Decimal(str(prob["difference"]))
        findings.append(_finding(
            "PDF-04",
            sheet=None,
            cells=[],
            key=f"p{prob['page']}:{prob['caption']}:{prob['column']}:{i}",
            caption=str(prob["caption"]),
            printed=str(prob["printed"]),
            expected=str(prob["sum"]),
            difference=diff,
            pages_in_pdf=[int(prob["page"])],  # type: ignore[call-overload]
            explanation=(
                f"Page {prob['page']}: '{prob['caption']}' prints {prob['printed']} but the printed lines above add to {prob['sum']} "
                f"(difference {diff}). Reconstructed from the PDF layout only."
            ),
            certainty=Certainty.REVIEW,
            fix_kind=FixKind.NONE,
            suggested_fix="Check the workbook total; PDF-only mode never proposes workbook edits.",
        ))
    for tok in result.error_tokens:
        findings.append(_finding("PDF-02", key=f"err:{tok}", explanation=f"Excel error value printed in the PDF: {tok}.", certainty=Certainty.LIKELY))
    if config is not None:
        for msg in period_problems(doc, config):
            result.text_mismatches.append(msg)
    return findings, result


__all__ = [
    "ERROR_TOKENS",
    "ReconState",
    "cast_pdf_tables",
    "check_pdf_only",
    "pdf_checks",
    "reconcile",
    "reconcile_ctx",
    "reprint_list",
    "verify",
]
