"""Key figures (PAT, PBT, TCI, net worth, working capital, cash …) per period.

Used by narrative / Going Concern checks, ratios, the partner summary and the
fix engine's "printed figures moved" highlights. Values are *displayed*
values (what the reader sees), in the statement unit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from fscore.engine.refs import make_addr
from fscore.models import CellRef, KeyFigure

if TYPE_CHECKING:
    from fscore.checks.base import CheckContext

LABELS = {
    "revenue": "Revenue from operations",
    "other_income": "Other income",
    "total_income": "Total income",
    "total_expenses": "Total expenses",
    "pbt": "Profit/(loss) before tax",
    "total_tax": "Total tax expense",
    "pat": "Profit/(loss) for the period",
    "oci_total": "Other comprehensive income",
    "tci": "Total comprehensive income",
    "eps_basic": "Earnings per share (basic)",
    "total_assets": "Total assets",
    "total_eq_liab": "Total equity and liabilities",
    "total_equity": "Total equity (net worth)",
    "share_capital": "Equity share capital",
    "other_equity": "Other equity",
    "total_ca": "Total current assets",
    "total_cl": "Total current liabilities",
    "working_capital": "Working capital (current assets − current liabilities)",
    "nca_ncl_gap": "Current liabilities in excess of current assets",
    "cash": "Cash and cash equivalents",
    "trade_receivables": "Trade receivables",
    "cf_closing": "Cash flow closing cash",
    "cf_opening": "Cash flow opening cash",
    "equity_infused": "Equity shares issued during the period",
}

PL_KEYS = ("revenue", "other_income", "total_income", "total_expenses", "pbt", "total_tax", "pat", "oci_total", "tci", "eps_basic")
BS_KEYS = ("total_assets", "total_eq_liab", "total_equity", "share_capital", "other_equity", "total_ca", "total_cl", "cash", "trade_receivables")
CF_KEYS = ("cf_closing", "cf_opening")


@dataclass
class KeyFigures:
    values: dict[tuple[str, str], KeyFigure] = field(default_factory=dict)

    def get(self, key: str, period: str = "CY") -> Decimal | None:
        kf = self.values.get((key, period))
        return None if kf is None else kf.value

    def ref(self, key: str, period: str = "CY") -> CellRef | None:
        kf = self.values.get((key, period))
        return None if kf is None else kf.ref

    def periods(self, key: str) -> list[str]:
        return [p for (k, p) in self.values if k == key]

    def as_list(self) -> list[KeyFigure]:
        return sorted(self.values.values(), key=lambda k: (k.key, k.period))


def compute_key_figures(ctx: CheckContext) -> KeyFigures:
    smap = ctx.statements
    kf = KeyFigures()

    def put(key: str, line_key: str | None = None) -> None:
        loc = smap.line(line_key or key)
        if loc is None:
            return
        sh = ctx.wb.sheet(loc.sheet)
        for period, col in loc.cols.items():
            v = ctx.displayed(sh, loc.row, col)
            if v is None:
                c = sh.cell(loc.row, col)
                if c is None or c.value in (None, ""):
                    v = Decimal(0) if c is not None else None
            if v is None:
                continue
            kf.values[(key, period)] = KeyFigure(
                key=key, label=LABELS.get(key, key), period=period, value=v,
                printed=ctx.printed_text(sh, loc.row, col), ref=CellRef(sheet=sh.name, addr=make_addr(loc.row, col)),
            )

    for k in PL_KEYS + BS_KEYS + CF_KEYS:
        put(k)
    # derived figures
    periods = {p for (_k, p) in kf.values}
    for p in periods:
        ca, cl = kf.get("total_ca", p), kf.get("total_cl", p)
        if ca is not None and cl is not None:
            kf.values[("working_capital", p)] = KeyFigure(key="working_capital", label=LABELS["working_capital"], period=p, value=ca - cl)
            kf.values[("nca_ncl_gap", p)] = KeyFigure(key="nca_ncl_gap", label=LABELS["nca_ncl_gap"], period=p, value=cl - ca)
        if kf.get("total_equity", p) is None:
            sc, oe = kf.get("share_capital", p), kf.get("other_equity", p)
            if sc is not None and oe is not None:
                kf.values[("total_equity", p)] = KeyFigure(key="total_equity", label=LABELS["total_equity"], period=p, value=sc + oe)
    # equity infused (SOCE issue rows): sum of printed numbers on the row, per period block
    for loc in smap.alternates.get("soce_issue", []):
        sh = ctx.wb.sheet(loc.sheet)
        from fscore.engine.periods import scope_key
        from fscore.engine.statements import column_label_at

        total = Decimal(0)
        found = False
        period = None
        for c in sh.iter_cells():
            if c.row != loc.row or not c.is_number or not ctx.is_printed(sh, c.row, c.col):
                continue
            hdr = ctx.column_header(sh, c.col, c.row)
            if hdr and "total" in hdr.lower():
                v = ctx.displayed(sh, c.row, c.col)
                if v is not None:
                    total, found = v, True
                    break
        # the period is the SOCE block the row belongs to: nearest "balance as at" row below
        for r in range(loc.row + 1, min(sh.max_row, loc.row + 15) + 1):
            cap = ctx.caption(sh, r).lower()
            if "balance as at" in cap or "as at" in cap:
                from fscore.engine.periods import parse_period

                lab = parse_period(cap)
                if lab is not None:
                    period = scope_key(lab, ctx.config)
                break
        _ = column_label_at
        if found and period:
            kf.values[("equity_infused", period)] = KeyFigure(key="equity_infused", label=LABELS["equity_infused"], period=period, value=total,
                                                              ref=CellRef(sheet=sh.name, addr=make_addr(loc.row, 1)))
    return kf
