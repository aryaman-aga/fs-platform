"""Text-block collection and mention verification shared by the narrative and Going Concern checks.

Verification (02 §6.8 step 4–5): a quoted figure is compared with the metric of
the same period on *displayed* values, with 0 tolerance in the same unit and
±0.5 in the last quoted digit when the text rounds into a larger unit. Statuses:
Matched, Rounded, Mismatch, Period mismatch, Stale, Unit suspicion,
Claim contradicted, Unrecognised.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from fscore.config import UNIT_DIVISORS
from fscore.engine.refs import make_addr
from fscore.narrative.extract import Mention, extract_mentions
from fscore.narrative.metrics import Metrics, MetricValue

if TYPE_CHECKING:
    from fscore.checks.base import CheckContext

RESULT_METRICS = ("pat", "pbt", "tci")
# metrics whose sign the narrative states in words (profit/loss, positive/negative); all others compare magnitudes
SIGNED_METRICS = ("pat", "pbt", "tci", "net_worth", "working_capital", "nca_ncl_gap", "retained_earnings")
POINT_METRICS = ("net_worth", "share_capital", "working_capital", "nca_ncl_gap", "cash", "retained_earnings",
                 "borrowings", "total_assets", "other_equity", "total_ca", "total_cl", "msme_due", "contingent")

_GC_TITLE = re.compile(r"going\s+concern", re.I)
_DIVISORS = dict(UNIT_DIVISORS)
_DIVISORS["billions"] = 1_000_000_000


@dataclass
class TextBlock:
    sheet: str
    row: int
    col: int
    text: str
    hidden: bool  # row/column hidden (will not print)
    formula: bool  # formula-driven narrative
    note_no: str | None = None
    note_title: str | None = None
    in_gc: bool = False
    has_numbers: bool = False  # the row carries printed figures: a table caption, not narrative

    @property
    def addr(self) -> str:
        return make_addr(self.row, self.col)


@dataclass
class Evaluation:
    status: str
    expected: MetricValue | None = None
    matched_metric: str | None = None
    matched_period: str | None = None
    note: str = ""
    candidates: list[MetricValue] = field(default_factory=list)


def gc_region(ctx: CheckContext) -> tuple[str, int, int] | None:
    """(sheet, first row, last row) of the Going Concern note, if present."""
    if "gc_region" in ctx.shared:
        return ctx.shared["gc_region"]  # type: ignore[no-any-return]
    region: tuple[str, int, int] | None = None
    smap = ctx.statements
    for note in sorted(smap.notes.values(), key=lambda n: (n.sheet, n.row)):
        if _GC_TITLE.search(note.title):
            region = (note.sheet, note.row, note.end_row or note.row + 60)
            break
    if region is None:
        for s in ctx.print_sheets:
            for c in s.iter_cells():
                if isinstance(c.value, str) and len(c.value) < 60 and _GC_TITLE.search(c.value) and c.col <= 3:
                    # heading: go to the next short bold heading or 60 rows
                    end = min(s.max_row, c.row + 60)
                    for n in smap.notes.values():
                        if n.sheet == s.name and n.row > c.row:
                            end = min(end, n.row - 1)
                    region = (s.name, c.row, end)
                    break
            if region:
                break
    ctx.shared["gc_region"] = region
    return region


def collect_text_blocks(ctx: CheckContext, *, min_len: int = 25) -> list[TextBlock]:
    """Every narrative text cell on the print sheets (visible and hidden rows), in reading order."""
    if "text_blocks" in ctx.shared:
        return ctx.shared["text_blocks"]  # type: ignore[no-any-return]
    smap = ctx.statements
    region = gc_region(ctx)
    notes_by_sheet: dict[str, list[Any]] = {}
    for n in smap.notes.values():
        notes_by_sheet.setdefault(n.sheet, []).append(n)
    blocks: list[TextBlock] = []
    for s in ctx.print_sheets:
        if not s.visible:
            continue
        for c in s.iter_cells():
            if not isinstance(c.value, str):
                continue
            text = c.value.strip()
            if len(text) < min_len:
                continue
            if not s.in_print_area(c.row, c.col):
                continue
            hidden = s.is_hidden(c.row, c.col)
            note = next((n for n in notes_by_sheet.get(s.name, []) if n.row <= c.row <= (n.end_row or n.row)), None)
            in_gc = bool(region and region[0] == s.name and region[1] <= c.row <= region[2])
            numbers = any(x.is_number and x.row == c.row and x.col > c.col for x in _row_cells(s, c.row))
            blocks.append(TextBlock(s.name, c.row, c.col, text, hidden, c.formula is not None,
                                    note.number if note else None, note.title if note else None, in_gc, numbers))
    blocks.sort(key=lambda b: (ctx.wb.sheet(b.sheet).index, b.row, b.col))
    ctx.shared["text_blocks"] = blocks
    return blocks


def _row_cells(sheet: Any, row: int) -> list[Any]:
    out = []
    for col in range(1, sheet.max_col + 1):
        c = sheet.cell(row, col)
        if c is not None:
            out.append(c)
    return out


def mentions_for(ctx: CheckContext, block: TextBlock) -> list[Mention]:
    key = f"mentions:{block.sheet}!{block.addr}"
    if key not in ctx.shared:
        ctx.shared[key] = extract_mentions(block.text, ctx.config)
    return ctx.shared[key]  # type: ignore[no-any-return]


def _tolerance(m: Mention, stmt_units: str) -> Decimal:
    """Allowed difference in statement units for the quote's precision and unit."""
    if m.unit in (None, stmt_units, "percent"):
        if m.decimals >= 2:
            return Decimal(0)
        return Decimal("0.5") * Decimal(1).scaleb(-m.decimals)
    if m.unit == "rupees":
        return Decimal("0.5") / Decimal(_DIVISORS.get(stmt_units, 1))
    factor = Decimal(_DIVISORS.get(m.unit, 1)) / Decimal(_DIVISORS.get(stmt_units, 1))
    return Decimal("0.5") * Decimal(1).scaleb(-m.decimals) * factor


def _close(a: Decimal, b: Decimal, tol: Decimal) -> bool:
    return abs(a - b) <= tol


def default_periods(ctx: CheckContext, metric: str) -> list[str]:
    scope_keys = [p.key for p in ctx.config.scope()]
    if metric in RESULT_METRICS or metric in ("revenue", "equity_infused", "total_income"):
        # unspecified "for the period": quarter and year are both candidates in interim layouts
        return [k for k in ("CY", "Q4_CY") if k in scope_keys] or scope_keys[:1]
    return [k for k in ("CY",) if k in scope_keys] or scope_keys[:1]


def evaluate(ctx: CheckContext, m: Mention, metrics: Metrics) -> Evaluation:
    """Verify one quoted amount against the statements."""
    if m.kind != "amount" or m.value is None:
        return Evaluation("Unrecognised" if m.metric is None else "Matched")
    if m.metric is None:
        return Evaluation("Unrecognised")
    stmt_units = ctx.config.units
    tol = _tolerance(m, stmt_units)
    metric_list = [m.metric, *[a for a in m.alt_metrics if a != m.metric]]
    out_of_scope = m.period is None and m.period_end is not None
    if m.period:
        periods = [m.period]
    elif out_of_scope:
        fb = fallback_period(ctx, m)
        periods = [fb] if fb else []
    else:
        periods = default_periods(ctx, m.metric)
    quoted = m.value
    if m.metric not in SIGNED_METRICS:
        metrics = _abs_view(metrics, metric_list)
        quoted = abs(quoted)
    ev = _evaluate(ctx, m, metrics, metric_list, periods, quoted, tol, stmt_units)
    if out_of_scope:
        ev.note = (ev.note + "; " if ev.note else "") + f"the text refers to {m.period_text}, outside the reporting periods"
    return ev


def _abs_view(metrics: Metrics, metric_list: list[str]) -> Metrics:
    """A copy of the metrics with absolute values for the given metrics (allowances, dues, cash …)."""
    from dataclasses import replace

    view = Metrics(dict(metrics.values))
    for (metric, period), mv in metrics.values.items():
        if metric in metric_list and mv.value < 0:
            view.values[(metric, period)] = replace(mv, value=abs(mv.value))
    return view


def fallback_period(ctx: CheckContext, m: Mention) -> str | None:
    """The in-scope period a stale or malformed date most likely meant (same year end first)."""
    scope = ctx.config.scope()
    if not scope or m.period_end is None:
        return None
    same_year = [p for p in scope if p.end.year == m.period_end.year]
    cands = same_year or sorted(scope, key=lambda p: p.end, reverse=True)
    want = m.period_months or 12
    best = next((p for p in cands if p.months == want), None) or next((p for p in cands if p.months == 12), None) or cands[0]
    return best.key


def _evaluate(ctx: CheckContext, m: Mention, metrics: Metrics, metric_list: list[str], periods: list[str],
              quoted: Decimal, tol: Decimal, stmt_units: str) -> Evaluation:
    candidates: list[MetricValue] = []
    # 1. same period, metric (and alternates such as PBT for "profit")
    for metric in metric_list:
        for p in periods:
            mv = metrics.get(metric, p)
            if mv is None:
                continue
            candidates.append(mv)
            same_unit = m.unit in (None, stmt_units)
            if _close(quoted, mv.value, Decimal(0)):
                if same_unit and m.decimals < 2 and not m.words:
                    return Evaluation("Rounded", mv, metric, p, "correct figure but quoted with fewer than 2 decimals", candidates)
                return Evaluation("Matched", mv, metric, p, candidates=candidates)
            if tol and _close(quoted, mv.value, tol):
                return Evaluation("Rounded" if same_unit else "Matched", mv, metric, p,
                                  "quoted with fewer decimals than the statements" if same_unit else "rounded into a larger unit",
                                  candidates)
            # result metrics quoted with the right magnitude but wrong sign → wording problem
            if metric in RESULT_METRICS and _close(abs(quoted), abs(mv.value), tol) and (quoted < 0) != (mv.value < 0) and mv.value != 0:
                return Evaluation("Claim contradicted", mv, metric, p, "profit/loss wording contradicts the sign", candidates)
    if not candidates:
        # no figure for the stated period: does it match another in-scope period?
        for metric in metric_list:
            for p in metrics.periods(metric):
                mv = metrics.get(metric, p)
                if mv is not None and (_close(quoted, mv.value, tol) or _close(abs(quoted), abs(mv.value), tol)):
                    return Evaluation("Period mismatch", mv, metric, p,
                                      f"figure belongs to {p}; the text refers to {m.period_text or 'another period'}", [mv])
        if m.period_end is not None and m.period is None:
            return Evaluation("Period mismatch", None, None, None, f"period '{m.period_text}' is outside the reporting periods", [])
        return Evaluation("Unrecognised", note="no statement figure for this metric")
    primary = candidates[0]
    # 2. stale: equals the previous-year figure of the same metric
    for metric in metric_list:
        for p in periods:
            pp = {"CY": "PY", "Q4_CY": "Q4_PY"}.get(p or "")
            if not pp:
                continue
            mv_py = metrics.get(metric, pp)
            if mv_py is not None and _close(quoted, mv_py.value, tol) and not _close(mv_py.value, primary.value, Decimal(0)):
                return Evaluation("Stale", primary, metric, p, f"equals the {pp} figure {mv_py.value}; probably not updated", candidates)
    # 3. matches the same metric of another in-scope period (e.g. quarter vs year)
    for metric in metric_list:
        for p in metrics.periods(metric):
            if p in periods:
                continue
            mv = metrics.get(metric, p)
            if mv is not None and _close(quoted, mv.value, tol):
                return Evaluation("Period mismatch", primary, metric, p, f"equals the {p} figure; the text refers to {periods[0] if periods else 'another period'}", candidates)
    # 4. unit suspicion (×/÷ 10, 100, 1000, 10^5, 10^7 …)
    for factor in (Decimal(10), Decimal(100), Decimal(1000), Decimal(100000), Decimal(10000000)):
        for mv in candidates:
            for test in (quoted * factor, quoted / factor):
                if mv.value != 0 and _close(test, mv.value, max(tol, abs(mv.value) * Decimal("0.0005"))):
                    return Evaluation("Unit suspicion", mv, mv.metric, mv.period, f"off by a factor of {factor}", candidates)
    return Evaluation("Mismatch", primary, primary.metric, primary.period, "", candidates)


def fmt(v: Decimal | None) -> str:
    if v is None:
        return ""
    q = v.quantize(Decimal("0.01"))
    s = f"{abs(q):,.2f}"
    return f"({s})" if q < 0 else s


def record(block: TextBlock, m: Mention, ev: Evaluation, *, rule: str | None = None) -> dict[str, Any]:
    """One row of ``ctx.shared['narrative']`` for the platform UI (green/red highlighting)."""
    return {
        "sheet": block.sheet,
        "cell": block.addr,
        "note": block.note_no,
        "in_going_concern": block.in_gc,
        "sentence": m.sentence,
        "quoted": m.text,
        "start": m.start,
        "end": m.end,
        "value": str(m.value) if m.value is not None else None,
        "unit": m.unit,
        "period": m.period,
        "period_text": m.period_text,
        "metric": m.metric,
        "status": ev.status,
        "matched_metric": ev.matched_metric,
        "matched_period": ev.matched_period,
        "expected": str(ev.expected.value) if ev.expected else None,
        "expected_printed": fmt(ev.expected.value) if ev.expected else None,
        "expected_cell": f"{ev.expected.ref.sheet}!{ev.expected.ref.addr}" if ev.expected and ev.expected.ref else None,
        "note_text": ev.note,
        "rule": rule,
    }
