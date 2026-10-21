"""Metrics the narrative may quote, computed from the statements of the same file (02 §6.8 step 3).

Values are *displayed* figures in the statement unit, per scope period
(``CY``, ``PY``, ``Q4_CY``, ``Q4_PY``), each with the cell it comes from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from fscore.engine.refs import make_addr
from fscore.engine.statements import norm_caption, period_columns_at
from fscore.models import CellRef

if TYPE_CHECKING:
    from fscore.checks.base import CheckContext

# metric -> key figure used directly
_FROM_KEY_FIGURES = {
    "pat": "pat",
    "pbt": "pbt",
    "tci": "tci",
    "revenue": "revenue",
    "net_worth": "total_equity",
    "share_capital": "share_capital",
    "working_capital": "working_capital",
    "nca_ncl_gap": "nca_ncl_gap",
    "cash": "cash",
    "equity_infused": "equity_infused",
    "total_assets": "total_assets",
    "other_equity": "other_equity",
    "total_ca": "total_ca",
    "total_cl": "total_cl",
    "total_income": "total_income",
}

# metric -> caption regex searched in the notes (first printed match wins)
_FROM_CAPTIONS: dict[str, re.Pattern[str]] = {
    "retained_earnings": re.compile(r"^(closing\s+)?(balance\s+of\s+)?retained\s+earnings$|^surplus\s+in\s+(the\s+)?statement\s+of\s+profit|^(deficit|retained\s+earnings)\s*-?\s*closing"),
    "msme_due": re.compile(r"principal\s+amount\s+(remaining\s+)?(due|unpaid)|^(total\s+outstanding\s+)?dues\s+of\s+micro\s+(and|&)\s+small\s+enterprises$|^micro\s+(and|&)\s+small\s+enterprises$"),
    "msme_interest": re.compile(r"interest\s+(due|payable|accrued).*(micro|msme|msmed)|interest\s+due\s+(thereon|on\s+above)"),
    "csr_obligation": re.compile(r"amount\s+required\s+to\s+be\s+spent|gross\s+amount\s+required"),
    "csr_spent": re.compile(r"amount\s+(of\s+expenditure\s+)?(spent|incurred)"),
    "remuneration": re.compile(r"managerial\s+remuneration|^remuneration(\s+to\s+(directors|kmp|key\s+managerial))?"),
    "provision_doubtful": re.compile(r"(provision|allowance)\s+for\s+(doubtful|expected\s+credit)|expected\s+credit\s+loss"),
    "contingent": re.compile(r"^total\s+contingent|contingent\s+liabilities\s*$"),
}

# retained earnings laid out as a movement block in the other-equity note: a heading without figures,
# closed a few rows below by the balance at the end of the period
_RETAINED_HEADING = re.compile(r"^(retained\s+earnings|(surplus|deficit)(\s*/\s*\(?deficit\)?)?\s+in\s+(the\s+)?statement\s+of\s+profit\b.*)$")
_CLOSING_BALANCE = re.compile(r"^(balance\s+(at|as\s+at)\s+(the\s+)?end\s+of\s+the\s+(year|period)|closing\s+balance)\b")


@dataclass(frozen=True)
class MetricValue:
    metric: str
    period: str
    value: Decimal
    printed: str = ""
    ref: CellRef | None = None
    source: str = ""


@dataclass
class Metrics:
    values: dict[tuple[str, str], MetricValue] = field(default_factory=dict)

    def get(self, metric: str, period: str | None) -> MetricValue | None:
        if period is None:
            return None
        return self.values.get((metric, period))

    def value(self, metric: str, period: str | None) -> Decimal | None:
        mv = self.get(metric, period)
        return None if mv is None else mv.value

    def periods(self, metric: str) -> list[str]:
        return sorted(p for (m, p) in self.values if m == metric)

    def put(self, mv: MetricValue) -> None:
        self.values.setdefault((mv.metric, mv.period), mv)

    def table(self) -> list[dict[str, str]]:
        """A small table of computed metrics (for the AI hand-off and the UI)."""
        return [
            {"metric": mv.metric, "period": mv.period, "value": str(mv.value), "printed": mv.printed,
             "cell": f"{mv.ref.sheet}!{mv.ref.addr}" if mv.ref else ""}
            for mv in sorted(self.values.values(), key=lambda x: (x.metric, x.period))
        ]


def row_values(ctx: CheckContext, sheet_name: str, row: int) -> dict[str, tuple[Decimal, int]]:
    """Printed values of a row per scope period: {period: (value, col)}."""
    sh = ctx.wb.get_sheet(sheet_name)
    if sh is None:
        return {}
    out: dict[str, tuple[Decimal, int]] = {}
    for period, col in period_columns_at(ctx, sh, row).items():
        if not ctx.is_printed(sh, row, col):
            continue
        v = ctx.displayed(sh, row, col)
        if v is None:
            c = sh.cell(row, col)
            if c is None or c.value in (None, ""):
                continue
            continue
        out[period] = (v, col)
    return out


def find_caption_rows(ctx: CheckContext, rx: re.Pattern[str], *, sheets: list[str] | None = None,
                      exclude: set[str] | None = None, printed_only: bool = True) -> list[tuple[str, int, str]]:
    """(sheet, row, caption) of rows whose normalised caption matches ``rx``."""
    out: list[tuple[str, int, str]] = []
    names = sheets or [s.name for s in ctx.print_sheets]
    for name in names:
        if exclude and name in exclude:
            continue
        sh = ctx.wb.get_sheet(name)
        if sh is None:
            continue
        seen_rows: set[int] = set()
        for c in sh.iter_cells():
            if c.row in seen_rows or not isinstance(c.value, str) or c.col > 4:
                continue
            if printed_only and not ctx.is_printed(sh, c.row, c.col):
                continue
            cap = ctx.caption(sh, c.row)
            if cap and rx.search(norm_caption(cap)):
                out.append((sh.name, c.row, cap))
                seen_rows.add(c.row)
    return out


def compute_metrics(ctx: CheckContext) -> Metrics:
    if "narrative_metrics" in ctx.shared:
        return ctx.shared["narrative_metrics"]  # type: ignore[no-any-return]
    m = Metrics()
    kf = ctx.key_figures
    for metric, key in _FROM_KEY_FIGURES.items():
        for period in kf.periods(key):
            v = kf.get(key, period)
            if v is None:
                continue
            ref = kf.ref(key, period)
            kfo = kf.values.get((key, period))
            m.put(MetricValue(metric, period, v, kfo.printed or "" if kfo else "", ref, f"key figure {key}"))
    statements = ctx.statements
    face = {statements.sheet("balance_sheet"), statements.sheet("profit_loss")}
    for metric, rx in _FROM_CAPTIONS.items():
        rows = find_caption_rows(ctx, rx, exclude={n for n in face if n})
        for sname, row, _cap in rows:
            vals = row_values(ctx, sname, row)
            for period, (v, col) in vals.items():
                m.put(MetricValue(metric, period, v, ctx.printed_text(ctx.wb.sheet(sname), row, col),
                                  CellRef(sheet=sname, addr=make_addr(row, col)), f"caption {metric}"))
            if vals:
                break
    if not m.periods("retained_earnings"):
        for sname, row, _cap in find_caption_rows(ctx, _RETAINED_HEADING, exclude={n for n in face if n}):
            if row_values(ctx, sname, row):
                continue
            sh = ctx.wb.sheet(sname)
            for r in range(row + 1, min(sh.max_row, row + 15) + 1):
                if not _CLOSING_BALANCE.search(norm_caption(ctx.caption(sh, r))):
                    continue
                for period, (v, col) in row_values(ctx, sname, r).items():
                    m.put(MetricValue("retained_earnings", period, v, ctx.printed_text(sh, r, col),
                                      CellRef(sheet=sname, addr=make_addr(r, col)), "retained earnings closing balance"))
                break
            if m.periods("retained_earnings"):
                break
    # borrowings: sum of every "Borrowings" line on the Balance Sheet (non-current + current)
    bs = statements.sheet("balance_sheet")
    if bs:
        totals: dict[str, Decimal] = {}
        refs: dict[str, CellRef] = {}
        for sname, row, _cap in find_caption_rows(ctx, re.compile(r"^borrowings$"), sheets=[bs]):
            for period, (v, col) in row_values(ctx, sname, row).items():
                totals[period] = totals.get(period, Decimal(0)) + v
                refs.setdefault(period, CellRef(sheet=sname, addr=make_addr(row, col)))
        for period, v in totals.items():
            m.put(MetricValue("borrowings", period, v, "", refs.get(period), "BS borrowings"))
    # equity infused fallback: "issued during the year" rows in the share-capital note (amount columns)
    if not m.periods("equity_infused"):
        rx = re.compile(r"(shares\s+)?issued\s+during\s+the\s+(year|period)|add:?\s*issued|equity\s+shares\s+issued")
        for sname, row, _cap in find_caption_rows(ctx, rx):
            vals = row_values(ctx, sname, row)
            sh_ = ctx.wb.sheet(sname)
            for period, (v, col) in vals.items():
                cell_ = sh_.cell(row, col)
                # amounts only (2-decimal formats); share counts use whole-number formats
                if cell_ is None or ctx.decimals(sh_, cell_) < 1:
                    continue
                if v != 0:
                    m.put(MetricValue("equity_infused", period, v, ctx.printed_text(ctx.wb.sheet(sname), row, col),
                                      CellRef(sheet=sname, addr=make_addr(row, col)), "issued during the year"))
            if vals:
                break
    ctx.shared["narrative_metrics"] = m
    return m
