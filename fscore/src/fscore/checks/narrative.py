"""Narrative notes (02 §6.8): NARR-01…05 and TXT-06.

Deterministic first: figures quoted in the notes' text are extracted, mapped to
metrics and verified against the statements of the same file. The Going
Concern note has its own, stricter rules (checks/going_concern.py); sentences
the engine cannot map are collected for the optional, approval-gated AI.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from rapidfuzz import fuzz

from fscore.models import Certainty, Evidence, Severity
from fscore.narrative.evaluate import (
    POINT_METRICS,
    RESULT_METRICS,
    TextBlock,
    collect_text_blocks,
    evaluate,
    fmt,
    mentions_for,
    record,
)
from fscore.narrative.extract import Mention, claims
from fscore.narrative.metrics import Metrics, compute_metrics, row_values
from fscore.registry import check

if TYPE_CHECKING:
    from fscore.checks.base import CheckContext

_STATUS_RULE = {
    "Mismatch": "NARR-01",
    "Claim contradicted": "NARR-02",
    "Stale": "NARR-03",
    "Unit suspicion": "NARR-05",
    "Period mismatch": "NARR-01",
}


def _evidence(ctx: CheckContext, block: TextBlock, m: Mention, expected: Any) -> list[Evidence]:
    from fscore.models import CellRef

    ev = [Evidence(label="Narrative", ref=CellRef(sheet=block.sheet, addr=block.addr), printed=m.text, value=m.value,
                   note=m.sentence[:300])]
    if expected is not None and expected.ref is not None:
        ev.append(Evidence(label=f"{expected.metric} ({expected.period})", ref=expected.ref, printed=expected.printed or fmt(expected.value),
                           value=expected.value))
    return ev


def _face_sheets(ctx: CheckContext) -> set[str]:
    smap = ctx.statements
    return {n for n in (smap.sheet("balance_sheet"), smap.sheet("profit_loss"), smap.sheet("cash_flow"), smap.sheet("soce")) if n}


def unresolved_sentences(ctx: CheckContext) -> list[dict[str, Any]]:
    """Sentences the engine could not map (for the optional AI hand-off, 02 §6.8 step 6)."""
    run_narrative(ctx)
    out = []
    seen: set[tuple[str, str, str]] = set()
    for r in ctx.shared.get("narrative", []):
        if r["status"] in ("Unrecognised",) and r.get("value") is not None:
            key = (r["sheet"], r["cell"], r["sentence"])
            if key in seen:
                continue
            seen.add(key)
            out.append({"sheet": r["sheet"], "cell": r["cell"], "note": r.get("note"), "sentence": r["sentence"],
                        "in_going_concern": r.get("in_going_concern", False)})
    return out


def run_narrative(ctx: CheckContext) -> None:
    """Evaluate every mention once (idempotent) and publish ctx.shared['narrative']."""
    if ctx.shared.get("_narrative_done"):
        return
    metrics = compute_metrics(ctx)
    rows: list[dict[str, Any]] = ctx.shared.setdefault("narrative", [])
    evaluations: list[tuple[TextBlock, Mention, Any]] = []
    face = _face_sheets(ctx)
    for block in collect_text_blocks(ctx):
        if block.hidden or block.in_gc or block.has_numbers or block.sheet in face:
            continue  # GC owns its note; captions of figure rows and face statements are not narrative
        for m in mentions_for(ctx, block):
            ev = evaluate(ctx, m, metrics) if m.kind == "amount" else None
            if ev is None:
                continue
            evaluations.append((block, m, ev))
            rows.append(record(block, m, ev))
    ctx.shared["narrative_evaluations"] = evaluations
    ctx.shared["_narrative_done"] = True


@check("NARR-01", "NARR-02", "NARR-03", "NARR-04", "NARR-05", "TXT-06", order=150)
def narrative_checks(ctx: CheckContext) -> None:
    run_narrative(ctx)
    metrics = compute_metrics(ctx)
    evaluations: list[tuple[TextBlock, Mention, Any]] = ctx.shared.get("narrative_evaluations", [])
    for block, m, ev in evaluations:
        if block.in_gc:
            continue  # Going Concern rules own that note
        rule = _STATUS_RULE.get(ev.status)
        if rule is None:
            continue
        if ev.status == "Period mismatch" and ev.expected is None:
            continue  # a date outside scope is reported by TXT-02 / GC-03, not as a figure mismatch
        exp = ev.expected
        severity = None
        certainty = None
        if rule == "NARR-01" and ev.status == "Period mismatch":
            severity, certainty = Severity.MEDIUM, Certainty.REVIEW
        explanation = {
            "Mismatch": f"The text quotes {m.text} for {m.metric} ({m.period or 'unstated period'}) but the statements show {fmt(exp.value) if exp else '?'}.",
            "Claim contradicted": f"The wording around {m.text} contradicts the sign of {m.metric}: the statements show {fmt(exp.value) if exp else '?'}.",
            "Stale": f"{m.text} equals last year's {m.metric}; the current figure is {fmt(exp.value) if exp else '?'} — the text was probably not updated.",
            "Unit suspicion": f"{m.text} is {ev.note} from {m.metric} {fmt(exp.value) if exp else '?'} — check the unit (lakhs / crores / millions / rupees).",
            "Period mismatch": f"{m.text} is the {ev.matched_period} figure of {ev.matched_metric}, but the sentence refers to {m.period_text or m.period}.",
        }[ev.status]
        ctx.emit(
            rule, sheet=block.sheet, cells=[block.addr], explanation=explanation,
            severity=severity, certainty=certainty,
            caption=(block.note_title or "")[:80] or None, printed=m.text,
            expected=fmt(exp.value) if exp else None,
            difference=(m.value - exp.value) if (exp is not None and m.value is not None) else None,
            evidence=_evidence(ctx, block, m, exp),
            suggested_fix=f"Replace {m.text!r} with {fmt(exp.value)} (typed text, 2 decimals)" if exp else None,
            period=m.period, key=f"{m.start}:{m.metric}",
        )
    _claims(ctx, metrics)
    _consistency(ctx, evaluations)
    _txt06(ctx, evaluations)


def _claims(ctx: CheckContext, metrics: Metrics) -> None:
    """NARR-02: qualitative claims that the figures contradict."""
    face = _face_sheets(ctx)
    for block in collect_text_blocks(ctx):
        if block.hidden or block.in_gc or block.has_numbers or block.sheet in face:
            continue
        for name, s, _e, txt in claims(block.text):
            reason = claim_contradiction(name, metrics, block.text, s)
            if reason:
                ctx.emit("NARR-02", sheet=block.sheet, cells=[block.addr], explanation=f"The note states '{txt}' but {reason}.",
                         printed=txt, caption=(block.note_title or "")[:80] or None, key=f"claim:{name}:{s}",
                         evidence=[Evidence(label="Narrative", printed=txt, note=block.text[:300])])


def claim_contradiction(name: str, metrics: Metrics, text: str, pos: int) -> str | None:
    """Reason a claim is contradicted, or None. Only unambiguous claims are judged."""
    eq = metrics.value("net_worth", "CY")
    ca, cl = metrics.value("total_ca", "CY"), metrics.value("total_cl", "CY")
    if name == "positive_net_worth" and eq is not None and eq <= 0:
        return f"total equity is {fmt(eq)}"
    if name in ("negative_net_worth",) and eq is not None and eq > 0:
        return f"total equity is positive ({fmt(eq)})"
    if name == "net_worth_eroded" and eq is not None and eq > 0 and re.search(r"\b(fully|completely|entirely)\b", text[pos:pos + 80], re.I):
        return f"total equity is positive ({fmt(eq)})"
    if name == "cl_exceed_ca" and ca is not None and cl is not None and cl <= ca:
        return f"current assets ({fmt(ca)}) exceed current liabilities ({fmt(cl)})"
    if name == "ca_exceed_cl" and ca is not None and cl is not None and ca <= cl:
        return f"current liabilities ({fmt(cl)}) exceed current assets ({fmt(ca)})"
    if name == "profits_last_n_years":
        losses = [p for p in metrics.periods("pat") if (metrics.value("pat", p) or 0) < 0]
        if losses:
            return f"a loss is reported for {', '.join(losses)}"
    if name in ("increase", "decrease"):
        return _trend_contradiction(name, metrics, text, pos)
    return None


_TREND_METRICS = {
    "revenue": re.compile(r"\brevenue|turnover|sales\b", re.I),
    "pat": re.compile(r"\bnet\s+profit|profit\s+(after\s+tax|for\s+the\s+year)\b", re.I),
    "net_worth": re.compile(r"\bnet\s*worth\b", re.I),
    "cash": re.compile(r"\bcash\s+and\s+cash\s+equivalents\b", re.I),
}


def _trend_contradiction(name: str, metrics: Metrics, text: str, pos: int) -> str | None:
    # only "<metric> increased/decreased" or "increase/decrease in <metric>" within a few words
    around = text[max(0, pos - 45):pos + 45]
    for metric, rx in _TREND_METRICS.items():
        m = rx.search(around)
        if not m:
            continue
        cy, py = metrics.value(metric, "CY"), metrics.value(metric, "PY")
        if cy is None or py is None or cy == py:
            return None
        if name == "increase" and cy < py:
            return f"{metric} decreased from {fmt(py)} to {fmt(cy)}"
        if name == "decrease" and cy > py:
            return f"{metric} increased from {fmt(py)} to {fmt(cy)}"
    return None


def _consistency(ctx: CheckContext, evaluations: list[tuple[TextBlock, Mention, Any]]) -> None:
    """NARR-04: the same metric and period quoted with different values in different places."""
    seen: dict[tuple[str, str], list[tuple[TextBlock, Mention]]] = {}
    for block, m, _ev in evaluations:
        if m.metric is None or m.value is None or m.period is None:
            continue
        if m.metric not in RESULT_METRICS + POINT_METRICS + ("revenue", "equity_infused"):
            continue
        seen.setdefault((m.metric, m.period), []).append((block, m))
    for (metric, period), items in seen.items():
        values = {abs(m.value) for _b, m in items if m.value is not None}
        if len(values) <= 1:
            continue
        blocks = {(b.sheet, b.addr) for b, _m in items}
        if len(blocks) < 2:
            continue
        first_b, first_m = items[0]
        ctx.emit(
            "NARR-04", sheet=first_b.sheet, cells=sorted({b.addr for b, _m in items if b.sheet == first_b.sheet}),
            explanation=f"{metric} for {period} is quoted with different values: " + "; ".join(f"{b.sheet}!{b.addr}: {m.text}" for b, m in items[:6]),
            printed=first_m.text, period=period, key=f"{metric}:{period}",
            evidence=[Evidence(label=f"{b.sheet}!{b.addr}", printed=m.text, value=m.value) for b, m in items[:10]],
        )


def _txt06(ctx: CheckContext, evaluations: list[tuple[TextBlock, Mention, Any]]) -> None:
    """TXT-06: numbers in a note's text that disagree with the same note's table (Review)."""
    for block, m, ev in evaluations:
        if block.in_gc or ev.status != "Unrecognised" or m.value is None or block.note_no is None:
            continue
        note = ctx.statements.notes.get(block.note_no)
        if note is None:
            continue
        sh = ctx.wb.sheet(note.sheet)
        phrase = m.sentence[: max(0, m.start - _sentence_offset(block.text, m))].strip()
        words = re.findall(r"[A-Za-z][A-Za-z'&-]+", phrase)[-7:]
        if len(words) < 2:
            continue
        probe = " ".join(words).lower()
        best: tuple[float, int] | None = None
        for row in range(note.row + 1, (note.end_row or note.row) + 1):
            cap = ctx.caption(sh, row)
            if not cap or len(cap) > 120:
                continue
            score = fuzz.partial_ratio(cap.lower(), probe)
            if score >= 90 and len(cap) >= 8 and (best is None or score > best[0]):
                best = (score, row)
        if best is None:
            continue
        vals = row_values(ctx, sh.name, best[1])
        if not vals:
            continue
        printed_values = {v for v, _c in vals.values()}
        if any(abs(m.value - v) <= Decimal("0.005") for v in printed_values):
            continue
        target = vals.get(m.period or "CY") or next(iter(vals.values()))
        ctx.emit("TXT-06", sheet=block.sheet, cells=[block.addr], certainty=Certainty.REVIEW,
                 explanation=f"The text of note {block.note_no} quotes {m.text}, but the table row '{ctx.caption(sh, best[1])}' shows {fmt(target[0])}.",
                 printed=m.text, expected=fmt(target[0]), key=f"txt06:{m.start}",
                 evidence=[ctx.evidence_for(sh, best[1], target[1])])


def _sentence_offset(text: str, m: Mention) -> int:
    return m.sentence_start
