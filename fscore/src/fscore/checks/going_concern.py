"""Going Concern note (02 §6.10): GC-01…06.

The GC note quotes figures that must agree, to the printed paisa, with the
statements of the same file; periods must be the reporting periods in scope;
the mitigating factors must print; and corrections are proposed as typed text
in two decimals (never formulas, never one-decimal rounding).
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from rapidfuzz import fuzz

from fscore.engine.periods import find_dates
from fscore.models import Certainty, Evidence, Severity, TextEdit
from fscore.narrative.evaluate import (
    RESULT_METRICS,
    TextBlock,
    collect_text_blocks,
    evaluate,
    fmt,
    gc_region,
    mentions_for,
    record,
)
from fscore.narrative.extract import Mention, claims, period_refs
from fscore.narrative.metrics import Metrics, compute_metrics
from fscore.narrative.rewrite import (
    Replacement,
    apply_replacements,
    format_amount,
    format_date_like,
    swap_polarity,
)
from fscore.registry import check

if TYPE_CHECKING:
    from fscore.checks.base import CheckContext

GC_METRICS = ("pat", "pbt", "tci", "share_capital", "net_worth", "working_capital", "nca_ncl_gap", "cash", "equity_infused",
              "retained_earnings", "borrowings", "revenue")

_MONTH_YEAR_PERIOD = re.compile(
    r"\b(?:(?:year|quarter|period|(?:three|six|nine|twelve|3|6|9|12)[\s-]*months?)\s+(?:ended|ending)|as\s+(?:at|on)|balance\s+as\s+at)\s+"
    r"(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?,?\s+(?P<yr>(?:19|20)\d{2})\b",
    re.I,
)
_QUARTER_MONTH_ONLY = re.compile(r"\bquarter\s+ended\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b(?!\s*\d)", re.I)
_ITEM = re.compile(r"^\s*\(?([a-h]|i{1,3}|iv|v|vi{0,3})\)\s+", re.I)
_GROUP = re.compile(r"\b(?:the|its|our)\s+Group\b|\bGroup's\b")
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _scope_dates(ctx: CheckContext) -> set[date]:
    return {p.end for p in ctx.config.scope()}


def _gc_blocks(ctx: CheckContext) -> list[TextBlock]:
    return [b for b in collect_text_blocks(ctx, min_len=12) if b.in_gc]


def _ref(block: TextBlock) -> Any:
    from fscore.models import CellRef

    return CellRef(sheet=block.sheet, addr=block.addr)


@check("GC-01", "GC-02", "GC-03", "GC-04", "GC-05", "GC-06", order=160)
def going_concern_checks(ctx: CheckContext) -> None:
    region = gc_region(ctx)
    if region is None:
        return
    metrics = compute_metrics(ctx)
    blocks = _gc_blocks(ctx)
    visible = [b for b in blocks if not b.hidden]
    narrative_rows: list[dict[str, Any]] = ctx.shared.setdefault("narrative", [])
    proposals: dict[tuple[str, str], list[Replacement]] = {}
    reasons: dict[tuple[str, str], list[str]] = {}

    def propose(block: TextBlock, reps: list[Replacement], why: str) -> None:
        if not reps:
            return
        proposals.setdefault((block.sheet, block.addr), []).extend(reps)
        reasons.setdefault((block.sheet, block.addr), []).append(why)

    # ------------------------------------------------------------ GC-01 / GC-02 figures
    for block in visible:
        for m in mentions_for(ctx, block):
            if m.kind != "amount" or m.value is None:
                continue
            ev = evaluate(ctx, m, metrics)
            narrative_rows.append(record(block, m, ev, rule=None))
            if m.metric not in GC_METRICS and ev.status != "Unrecognised":
                continue
            _figure_findings(ctx, block, m, ev, propose)
    # ------------------------------------------------------------ GC-02 claims
    for block in visible:
        _claim_findings(ctx, block, metrics)
    _profit_and_loss_same_period(ctx, visible)
    # ------------------------------------------------------------ GC-03 period scope
    for block in visible:
        _period_findings(ctx, block, propose)
    # ------------------------------------------------------------ GC-04 structure
    _structure_findings(ctx, blocks, visible, propose)
    # ------------------------------------------------------------ GC-05 consistency with other notes
    _consistency_findings(ctx, metrics, visible)
    # ------------------------------------------------------------ GC-06 proposals (typed text)
    text_props: list[TextEdit] = ctx.shared.setdefault("text_proposals", [])
    for block in blocks:
        key = (block.sheet, block.addr)
        if key not in proposals and not block.formula:
            continue
        new_text = apply_replacements(block.text, proposals.get(key, []))
        if block.formula and key not in proposals:
            why = ["narrative is formula-driven; firm policy requires typed text"]
        else:
            why = reasons.get(key, [])
        if new_text == block.text and not block.formula:
            continue
        te = TextEdit(sheet=block.sheet, cell=block.addr, new_text=new_text, reason="; ".join(dict.fromkeys(why))[:500])
        if not any(t.sheet == te.sheet and t.cell == te.cell for t in text_props):
            text_props.append(te)
        ctx.emit("GC-06", sheet=block.sheet, cells=[block.addr],
                 explanation="Proposed corrected wording for the Going Concern note (typed text, 2 decimals, same sentence structure): "
                 + "; ".join(dict.fromkeys(why))[:400],
                 printed=block.text[:200], suggested_fix=new_text, key="gc06")


def _figure_findings(ctx: CheckContext, block: TextBlock, m: Mention, ev: Any, propose: Any) -> None:
    exp = ev.expected
    status = ev.status
    if status in ("Matched", "Unrecognised"):
        return
    rel = m.start - m.sentence_start
    if status == "Claim contradicted" and exp is not None and m.metric in RESULT_METRICS:
        want = "loss" if exp.value < 0 else "profit"
        ctx.emit("GC-02", sheet=block.sheet, cells=[block.addr],
                 explanation=f"The note describes {m.text} as a {'loss' if (m.value or 0) < 0 else 'profit'}, but the statements show a {want} of {fmt(abs(exp.value))} for {exp.period}.",
                 printed=m.text, expected=f"{want} of {fmt(abs(exp.value))}", period=exp.period, key=f"sign:{m.start}",
                 evidence=[Evidence(label="Going Concern", ref=_ref(block), printed=m.text, note=m.sentence[:300]),
                           Evidence(label=f"{exp.metric} ({exp.period})", ref=exp.ref, printed=exp.printed, value=exp.value)])
        propose(block, swap_polarity(block.text, m.start, m.end, to=want), f"{want} wording for {exp.period}")
        return
    if exp is None:
        return  # out-of-scope period with no comparable figure: GC-03 reports it
    severity = Severity.HIGH
    certainty = Certainty.LIKELY
    if status == "Rounded":
        same_value = m.value is not None and m.value == exp.value
        severity, certainty = (Severity.LOW if same_value else Severity.MEDIUM), Certainty.CERTAIN
        what = (f"{m.text} is quoted with fewer than 2 decimals; write {fmt(exp.value)} (statement unit and precision)." if same_value
                else f"{m.text} is rounded; the statements show {fmt(exp.value)} — quote 2 decimals in the statement unit, never 1.")
    elif status == "Stale":
        what = f"{m.text} is last year's figure (not updated); the statements show {fmt(exp.value)} for {exp.period}."
    elif status == "Period mismatch":
        what = f"{m.text} is the {ev.matched_period} figure; the text refers to {m.period_text or m.period}, for which the statements show {fmt(exp.value)}."
    elif status == "Unit suspicion":
        what = f"{m.text} differs from {fmt(exp.value)} by a unit factor ({ev.note})."
    else:
        what = f"The note quotes {m.text} for {m.metric} ({exp.period}) but the statements show {fmt(exp.value)}."
    if m.malformed:
        what += f" The figure '{m.number_text}' is also malformed (decimal comma)."
    ctx.emit("GC-01", sheet=block.sheet, cells=[block.addr], severity=severity, certainty=certainty, explanation=what,
             printed=m.text, expected=fmt(exp.value), period=exp.period,
             difference=(m.value - exp.value) if m.value is not None else None,
             evidence=[Evidence(label="Going Concern", ref=_ref(block), printed=m.text, value=m.value, note=m.sentence[:300]),
                       Evidence(label=f"{exp.metric} ({exp.period})", ref=exp.ref, printed=exp.printed, value=exp.value)],
             suggested_fix=f"Replace '{m.number_text}' with {format_amount(exp.value, style_of=m.number_text)}",
             key=f"fig:{m.start}")
    # replace the number as written (keep currency and unit words), then the wording if the sign is wrong
    num_start = block.text.find(m.number_text, m.start)
    if num_start >= 0:
        brackets = m.number_text.strip().startswith("(")
        propose(block, [Replacement(num_start, num_start + len(m.number_text),
                                    format_amount(exp.value, style_of=m.number_text, brackets=brackets and exp.value < 0),
                                    f"{m.metric} {exp.period} = {fmt(exp.value)}")], f"{m.metric} figure")
    if exp.metric in RESULT_METRICS and m.polarity and ((m.polarity == "loss") != (exp.value < 0)) and exp.value != 0:
        want = "loss" if exp.value < 0 else "profit"
        ctx.emit("GC-02", sheet=block.sheet, cells=[block.addr],
                 explanation=f"The note says '{m.polarity}' next to {m.text} but the statements show a {want} for {exp.period}.",
                 printed=m.text, expected=f"{want} of {fmt(abs(exp.value))}", period=exp.period, key=f"sign:{m.start}")
        propose(block, swap_polarity(block.text, m.start, m.end, to=want), f"{want} wording")
    _ = rel


def _claim_findings(ctx: CheckContext, block: TextBlock, metrics: Metrics) -> None:
    for name, _s, _e, txt in claims(block.text):
        eq = metrics.value("net_worth", "CY")
        if name == "positive_net_worth" and eq is not None and eq <= 0:
            ctx.emit("GC-02", sheet=block.sheet, cells=[block.addr], explanation=f"The note claims '{txt}' but total equity is {fmt(eq)}.",
                     printed=txt, key=f"claim:{name}")
        if name in ("negative_net_worth",) and eq is not None and eq > 0:
            ctx.emit("GC-02", sheet=block.sheet, cells=[block.addr], explanation=f"The note claims '{txt}' but total equity is positive ({fmt(eq)}).",
                     printed=txt, key=f"claim:{name}")
        if name == "cl_exceed_ca":
            ca, cl = metrics.value("total_ca", "CY"), metrics.value("total_cl", "CY")
            if ca is not None and cl is not None and cl <= ca:
                ctx.emit("GC-02", sheet=block.sheet, cells=[block.addr],
                         explanation=f"The note says current liabilities exceed current assets, but current assets ({fmt(ca)}) exceed current liabilities ({fmt(cl)}).",
                         printed=txt, key=f"claim:{name}")


def _claim_period(ctx: CheckContext, text: str, pos: int) -> str | None:
    refs = [r for r in period_refs(text, ctx.config) if r.key]
    if not refs:
        return "CY"
    return min(refs, key=lambda r: abs(r.start - pos)).key


def _profit_and_loss_same_period(ctx: CheckContext, visible: list[TextBlock]) -> None:
    """GC-02: the note must not say both 'earned profit' and 'incurred losses' for the same period."""
    said: dict[str, dict[str, tuple[TextBlock, str]]] = {}
    for block in visible:
        for name, s, _e, txt in claims(block.text):
            if name not in ("earned_profit", "incurred_loss"):
                continue
            # judge only the result of the sentence's own period (not "profits in past years")
            sent_start = max(block.text.rfind(".", 0, s), 0)
            sent = block.text[sent_start: block.text.find(".", s) if block.text.find(".", s) > 0 else len(block.text)]
            if re.search(r"\b(past|previous|earlier|preceding)\s+(years|periods)\b|\baccumulated\b", sent, re.I):
                continue
            per = _claim_period(ctx, block.text, s)
            if per is None:
                continue
            said.setdefault(per, {})[name] = (block, txt)
    for per, d in said.items():
        if "earned_profit" in d and "incurred_loss" in d:
            b1, t1 = d["earned_profit"]
            b2, t2 = d["incurred_loss"]
            ctx.emit("GC-02", sheet=b1.sheet, cells=sorted({b1.addr, b2.addr}),
                     explanation=f"The note says both '{t1}' and '{t2}' for the same period ({per}).",
                     printed=f"{t1} / {t2}", period=per, key=f"both:{per}")


def _period_findings(ctx: CheckContext, block: TextBlock, propose: Any) -> None:
    scope = _scope_dates(ctx)
    text = block.text
    flagged: set[tuple[int, int]] = set()
    for pr in period_refs(text, ctx.config):
        if pr.end_date is None:
            continue
        if pr.malformed:
            target = _scope_replacement(ctx, pr.end_date, pr.months)
            ctx.emit("GC-03", sheet=block.sheet, cells=[block.addr], explanation=f"Malformed date '{pr.text}' in the Going Concern note.",
                     printed=pr.text, expected=target.strftime("%B %d, %Y") if target else None, key=f"bad:{pr.start}")
            flagged.add((pr.start, pr.end))
            if target is not None:
                _propose_date(block, text, pr.text, target, propose)
            continue
        if pr.end_date in scope and pr.key is not None:
            continue
        if pr.end_date in scope and pr.key is None:
            # right date, but a duration the entity does not report (e.g. "year ended" for a three-month entity)
            ctx.emit("GC-03", sheet=block.sheet, cells=[block.addr],
                     explanation=f"'{pr.text}' is not a reporting period of this entity (check 'three months' vs 'year').",
                     printed=pr.text, key=f"dur:{pr.start}")
            continue
        target = _scope_replacement(ctx, pr.end_date, pr.months)
        ctx.emit("GC-03", sheet=block.sheet, cells=[block.addr],
                 explanation=f"'{pr.text}' refers to {pr.end_date:%d-%b-%Y}, outside the reporting periods ({', '.join(sorted(d.strftime('%d-%b-%Y') for d in scope))}).",
                 printed=pr.text, expected=target.strftime("%B %d, %Y") if target else None, key=f"scope:{pr.start}",
                 suggested_fix=f"Use {target:%B %d, %Y}" if target else None)
        flagged.add((pr.start, pr.end))
        if target is not None:
            _propose_date(block, text, pr.text, target, propose)
    # month-year periods ("year ended June 2026") and "quarter ended June" without a year
    for m in _MONTH_YEAR_PERIOD.finditer(text):
        if any(a <= m.start() < b for a, b in flagged):
            continue
        mon = _MONTHS.get(m.group("mon").lower()[:3])
        yr = int(m.group("yr"))
        if mon is None:
            continue
        if any(d.year == yr and d.month == mon for d in scope):
            continue
        ctx.emit("GC-03", sheet=block.sheet, cells=[block.addr],
                 explanation=f"'{m.group(0)}' is outside the reporting periods.", printed=m.group(0), key=f"my:{m.start()}")
        target = _scope_replacement(ctx, date(yr, mon, 28), None)
        if target is not None:
            s0 = m.start("mon")
            propose(block, [Replacement(s0, m.end("yr"), f"{target:%B} {target.year}", "reporting period")], "period scope")
    for m in _QUARTER_MONTH_ONLY.finditer(text):
        mon = _MONTHS.get(m.group(1).lower()[:3])
        if mon is not None and not any(d.month == mon for d in scope):
            ctx.emit("GC-03", sheet=block.sheet, cells=[block.addr], explanation=f"'{m.group(0)}' is not a quarter in scope.",
                     printed=m.group(0), key=f"q:{m.start()}")
    # three-month-only entity must say "three-month period"
    flows_12m = any(p.months == 12 for p in ctx.config.scope())
    if not flows_12m and re.search(r"\byear\s+ended\b", text, re.I):
        ctx.emit("GC-03", sheet=block.sheet, cells=[block.addr],
                 explanation="This entity reports a three-month period only; the note must say 'three-month period' rather than 'year ended'.",
                 key="three-month")
    for _d, bad, raw in find_dates(text):
        if bad and not any(raw in text[a:b] for a, b in flagged):
            ctx.emit("GC-03", sheet=block.sheet, cells=[block.addr], explanation=f"Malformed date '{raw}'.", printed=raw, key=f"bad2:{raw}")


_DATE_IN = re.compile(r"(\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?[A-Za-z]{3,9}\.?,?\s*\d{4}|[A-Za-z]{3,9}\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s*\d{4}|\d{1,2}[./-]\d{1,2}[./-]\d{4})")


def _propose_date(block: TextBlock, text: str, phrase: str, target: date, propose: Any) -> None:
    m = _DATE_IN.search(phrase)
    at = text.find(phrase)
    if m and at >= 0:
        s0 = at + m.start()
        propose(block, [Replacement(s0, s0 + len(m.group(0)), format_date_like(m.group(0), target), "reporting period")], "period scope")


def _scope_replacement(ctx: CheckContext, d: date, months: int | None) -> date | None:
    """The in-scope date a stale reference most likely meant (same year end first, else CY end)."""
    scope = ctx.config.scope()
    if not scope:
        return None
    cands = sorted({p.end for p in scope}, reverse=True)
    for e in cands:
        if e.year == d.year:
            return e
    return cands[0]


def _structure_findings(ctx: CheckContext, blocks: list[TextBlock], visible: list[TextBlock], propose: Any) -> None:
    # mitigating factors (a)… must print
    items_visible: list[str] = []
    for b in blocks:
        m = _ITEM.match(b.text)
        if not m:
            continue
        label = m.group(1).lower()
        if b.hidden:
            ctx.emit("GC-04", sheet=b.sheet, cells=[b.addr], severity=Severity.HIGH,
                     explanation=f"Mitigating factor ({label}) is in a hidden row/column and will not print.",
                     printed=b.text[:120], key=f"hidden:{label}:{b.row}",
                     suggested_fix="Unhide the row (and check it is still accurate) or remove the factor from the note's text.")
        else:
            items_visible.append(label)
    letters = [x for x in items_visible if len(x) == 1 and x.isalpha()]
    if letters:
        expected = [chr(ord("a") + i) for i in range(len(letters))]
        if letters != expected and sorted(set(letters)) != expected:
            missing = sorted(set("abcdefgh"[: ord(max(letters)) - ord("a") + 1]) - set(letters))
            if missing:
                ctx.emit("GC-04", sheet=visible[0].sheet if visible else blocks[0].sheet, cells=[],
                         explanation=f"Mitigating factors are lettered {', '.join(letters)}: ({', '.join(missing)}) missing from the printed note.",
                         key="gap")
    # duplicated / contradictory paragraphs
    for i, a in enumerate(visible):
        for b in visible[i + 1:]:
            if len(a.text) > 60 and fuzz.ratio(a.text, b.text) >= 92:
                ctx.emit("GC-04", sheet=a.sheet, cells=[a.addr, b.addr], severity=Severity.MEDIUM,
                         explanation="Two paragraphs of the Going Concern note are (almost) identical.", key=f"dup:{a.addr}:{b.addr}")
    all_claims = {name for b in visible for name, *_ in claims(b.text)}
    if "material_uncertainty" in all_claims and "no_material_uncertainty" in all_claims:
        ctx.emit("GC-04", sheet=visible[0].sheet, cells=[b.addr for b in visible][:4],
                 explanation="The note both states that a material uncertainty exists and that none exists.", key="contradiction")
    # standalone note must say "Company", not "Group"
    if ctx.config.standalone:
        for b in visible:
            for m in _GROUP.finditer(b.text):
                ctx.emit("GC-04", sheet=b.sheet, cells=[b.addr], severity=Severity.MEDIUM,
                         explanation=f"Standalone financial statements: '{m.group(0)}' should refer to the Company.",
                         printed=m.group(0), key=f"group:{m.start()}")
                new = re.sub(r"Group", "Company", m.group(0))
                propose(b, [Replacement(m.start(), m.end(), new, "standalone statements")], "Company, not Group")


_TAX_LOSS = re.compile(r"\b(?:company|entity)\s+(?:is|was|has\s+been)\s+(?:in|incurring)\s+(?:a\s+)?loss(?:es)?\b|\bin\s+view\s+of\s+(?:the\s+)?losses?\s+(?:for|during)\s+the\s+(?:current\s+)?(?:year|period)\b|\bno\s+(?:current\s+)?tax\b[^.]{0,60}\blosses?\s+(?:for|during)\s+the\s+(?:current\s+)?(?:year|period)\b", re.I)


def _consistency_findings(ctx: CheckContext, metrics: Metrics, visible: list[TextBlock]) -> None:
    pat = metrics.value("pat", "CY")
    if pat is None:
        pat = metrics.value("pbt", "CY")
    # tax note says the company is in loss in a profit year
    if pat is not None and pat > 0:
        for b in collect_text_blocks(ctx):
            if b.in_gc or b.hidden or not b.note_title or not re.search(r"\btax", b.note_title, re.I):
                continue
            m = _TAX_LOSS.search(b.text)
            if m:
                ctx.emit("GC-05", sheet=b.sheet, cells=[b.addr],
                         explanation=f"The tax note says '{m.group(0)}' although the statements show a profit of {fmt(pat)} for the year.",
                         printed=m.group(0), key=f"tax:{b.addr}")
    # equity-infusion factor = SOCE and the RPT note
    soce = metrics.value("equity_infused", "CY")
    rpt_equity: Decimal | None = ctx.shared.get("rpt_equity_issue_cy")
    for b in visible:
        for men in mentions_for(ctx, b):
            if men.metric != "equity_infused" or men.value is None:
                continue
            q = abs(men.value)
            if soce is not None and abs(q - abs(soce)) > Decimal("0.005"):
                # GC-01 already reports the figure; GC-05 records the cross-note inconsistency
                ctx.emit("GC-05", sheet=b.sheet, cells=[b.addr],
                         explanation=f"The equity-infusion factor quotes {men.text} but the SOCE shows {fmt(soce)} issued during the year.",
                         printed=men.text, expected=fmt(soce), key=f"eq-soce:{men.start}")
            if rpt_equity is not None and abs(q - abs(rpt_equity)) > Decimal("0.005"):
                ctx.emit("GC-05", sheet=b.sheet, cells=[b.addr],
                         explanation=f"The equity-infusion factor quotes {men.text} but the related-party note shows {fmt(rpt_equity)}.",
                         printed=men.text, expected=fmt(rpt_equity), key=f"eq-rpt:{men.start}")
