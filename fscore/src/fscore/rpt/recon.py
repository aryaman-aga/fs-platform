"""Three-way related-party reconciliation (RPT-09): RPT note vs trial balance vs FS note line.

One grid row per counterparty × nature × period, with variances and flags:
omitted balance, wrong counterparty, FY = Q4 (stale copy), gross vs net,
classification switch between years, debit/credit presentation, unsupported
foreign-currency amounts. FS note lines are compared at nature level (the
statements show related-party lines, not every counterparty).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from fscore.engine.refs import make_addr
from fscore.narrative.metrics import find_caption_rows, row_values
from fscore.rpt.ledger import RPTLedger
from fscore.rpt.names import best_match, normalise, similarity
from fscore.rpt.natures import BALANCE_NATURES, family
from fscore.rpt.note import RPTNote, RPTRow

if TYPE_CHECKING:
    from fscore.checks.base import CheckContext

TOL = Decimal("0.01")

# FS note lines that carry related-party amounts, per nature family
FS_LINES: dict[str, re.Pattern[str]] = {
    "trade_payable": re.compile(r"^(trade\s+)?(payables?|dues?|amounts?\s+payable)\s+(to|towards)\s+(related\s+part|holding|group|fellow)|^trade\s+payables?\s*[-–(]\s*related\s+part"),
    "trade_receivable": re.compile(r"^(trade\s+)?receivables?\s+from\s+(related\s+part|holding|group|fellow)|^trade\s+receivables?\s*[-–(]\s*related\s+part|^(unsecured,?\s+)?considered\s+good\s*[-–(]\s*related"),
    "loan_payable": re.compile(r"^(unsecured\s+)?loans?\s+from\s+(the\s+)?(holding|parent|related|group|ultimate|fellow)|^inter[\s-]*corporate\s+loans?\s+from"),
    "recoverable": re.compile(r"^(amounts?\s+)?recoverable\s+from\s+(group|related|holding|fellow)|^receivable\s+from\s+group\s+compan"),
    "services_rendered": re.compile(r"^(support\s+)?services?\s+(to|rendered\s+to)\s+(group|related|holding|fellow)|^revenue\s+from\s+related\s+part|^income\s+from\s+group"),
    "services_received": re.compile(r"^(support\s+)?services?\s+(from|received\s+from)\s+(group|related|holding)|^(management|support)\s+fees\s+to\s+(group|holding)"),
    "interest_paid": re.compile(r"^interest\s+on\s+(loans?|borrowings?)\s+from\s+(the\s+)?(holding|parent|related|group|fellow)"),
}


@dataclass
class GridRow:
    counterparty: str
    nature: str
    period: str
    rpt_value: Decimal | None = None
    rpt_cell: str | None = None
    rpt_party_as_written: str | None = None
    rpt_row: int | None = None
    tb_value: Decimal | None = None
    tb_accounts: list[str] = field(default_factory=list)
    fs_value: Decimal | None = None
    fs_cell: str | None = None
    status: str = "Agreed"
    flags: list[str] = field(default_factory=list)

    @property
    def variance(self) -> Decimal | None:
        if self.rpt_value is None and self.tb_value is None:
            return None
        return (self.rpt_value or Decimal(0)) - (self.tb_value or Decimal(0))

    def as_dict(self) -> dict[str, Any]:
        return {
            "counterparty": self.counterparty, "nature": self.nature, "period": self.period,
            "rpt_value": str(self.rpt_value) if self.rpt_value is not None else None, "rpt_cell": self.rpt_cell,
            "rpt_party_as_written": self.rpt_party_as_written,
            "tb_value": str(self.tb_value) if self.tb_value is not None else None, "tb_accounts": self.tb_accounts,
            "fs_value": str(self.fs_value) if self.fs_value is not None else None, "fs_cell": self.fs_cell,
            "variance": str(self.variance) if self.variance is not None else None,
            "status": self.status, "flags": list(self.flags),
        }


def fs_nature_values(ctx: CheckContext, note: RPTNote | None) -> dict[str, dict[str, tuple[Decimal, str]]]:
    """FS note-line values per nature family and period: {family: {period: (value, 'Sheet!A1')}}."""
    if "rpt_fs_lines" in ctx.shared:
        return ctx.shared["rpt_fs_lines"]  # type: ignore[no-any-return]
    out: dict[str, dict[str, tuple[Decimal, str]]] = {}
    exclude = {note.sheet} if note is not None else set()
    face = {ctx.statements.sheet("balance_sheet"), ctx.statements.sheet("profit_loss")}
    for fam, rx in FS_LINES.items():
        rows = find_caption_rows(ctx, rx, exclude=exclude | {f for f in face if f})
        acc: dict[str, tuple[Decimal, str]] = {}
        for sname, row, _cap in rows:
            if note is not None and sname == note.sheet and note.r1 <= row <= note.r2:
                continue
            for period, (v, col) in row_values(ctx, sname, row).items():
                prev = acc.get(period)
                acc[period] = ((prev[0] if prev else Decimal(0)) + v, prev[1] if prev else f"{sname}!{make_addr(row, col)}")
        if acc:
            out[fam] = acc
    ctx.shared["rpt_fs_lines"] = out
    return out


def _canonical_party(name: str, known: list[str]) -> str:
    m, _s = best_match(name, known, threshold=90)
    return m or name


def build_grid(ctx: CheckContext, note: RPTNote | None, ledger: RPTLedger) -> list[GridRow]:
    if "rpt_grid" in ctx.shared:
        return ctx.shared["rpt_grid"]  # type: ignore[no-any-return]
    grid: dict[tuple[str, str, str], GridRow] = {}
    known = list(note.party_names()) if note is not None else []
    for r in ledger.rows:
        if r.counterparty not in known:
            known.append(r.counterparty)
    periods = [p.key for p in ctx.config.scope()]
    if note is not None:
        for row in note.rows:
            if row.hidden:
                continue
            cp = _canonical_party(row.party, known)
            for period, v in row.values.items():
                key = (cp, family(row.nature), period)
                g = grid.setdefault(key, GridRow(cp, family(row.nature), period))
                g.rpt_value = (g.rpt_value or Decimal(0)) + v
                g.rpt_cell = g.rpt_cell or f"{row.sheet}!{row.cells.get(period, '')}"
                g.rpt_party_as_written = row.party
                g.rpt_row = g.rpt_row or row.row
    for lr in ledger.rows:
        cp = _canonical_party(lr.counterparty, known)
        for period in periods:
            if period not in lr.values:
                continue
            key = (cp, family(lr.nature), period)
            g = grid.setdefault(key, GridRow(cp, family(lr.nature), period))
            g.tb_value = (g.tb_value or Decimal(0)) + lr.values[period]
            g.tb_accounts.extend(a for a in lr.accounts if a not in g.tb_accounts)
    fs = fs_nature_values(ctx, note)
    for (_cp, fam, period), g in grid.items():
        fsv = fs.get(fam, {}).get(period)
        if fsv is not None:
            g.fs_value, g.fs_cell = fsv
    # statuses and flags
    rows = sorted(grid.values(), key=lambda g: (g.nature, g.counterparty, g.period))
    by_nature_period: dict[tuple[str, str], list[GridRow]] = {}
    for g in rows:
        by_nature_period.setdefault((g.nature, g.period), []).append(g)
    for g in rows:
        rv, tv = g.rpt_value, g.tb_value
        if tv is not None and abs(tv) >= TOL and (rv is None or rv == 0) and g.nature in ("equity_balance", "investment_balance"):
            g.status = "TB only (capital)"  # capital balances are covered by RPT-10 (capital transactions)
        elif tv is not None and abs(tv) >= TOL and (rv is None or rv == 0):
            # wrong counterparty: the same amount appears against another party in the note
            twin = next((o for o in by_nature_period[(g.nature, g.period)] if o is not g and o.rpt_value is not None
                         and abs(abs(o.rpt_value) - abs(tv)) <= TOL and (o.tb_value is None or o.tb_value == 0)), None)
            if twin is not None:
                # reported once, on the note's row (which carries the printed cell)
                g.status = "Wrong counterparty (TB side)"
                g.flags.append(f"the RPT note shows this amount against {twin.counterparty}")
            else:
                g.status = "Omitted"
                g.flags.append("omitted from the RPT note" if g.nature in BALANCE_NATURES or g.nature in ("trade_payable", "trade_receivable", "loan_payable", "recoverable") else "transaction omitted from the RPT note")
        elif rv is not None and abs(rv) >= TOL and (tv is None or tv == 0) and ledger.sheet is not None and ledger.accounts:
            twin = next((o for o in by_nature_period[(g.nature, g.period)] if o is not g and o.tb_value is not None
                         and abs(abs(o.tb_value) - abs(rv)) <= TOL and (o.rpt_value is None or o.rpt_value == 0)), None)
            if twin is not None:
                g.status = "Wrong counterparty"
                g.flags.append(f"the trial balance records this amount against {twin.counterparty}")
            else:
                g.status = "No TB support"
                g.flags.append("no trial-balance support")
        elif rv is not None and tv is not None and abs(rv - tv) > TOL:
            g.status = "Difference"
            lrow = next((x for x in ledger.rows if _canonical_party(x.counterparty, known) == g.counterparty and family(x.nature) == g.nature), None)
            if lrow is not None and g.period in lrow.gross_values and abs(lrow.gross_values[g.period] - rv) <= TOL:
                g.flags.append("gross vs net: the note shows the gross amount, the TB / P&L net a recovery")
            if lrow is not None and g.period in lrow.fc_values and abs(lrow.fc_values[g.period] - rv) <= TOL:
                g.flags.append("unsupported foreign-currency amount: the note quotes the FC figure")
        lr2 = next((x for x in ledger.rows if _canonical_party(x.counterparty, known) == g.counterparty and family(x.nature) == g.nature), None)
        if lr2 is not None and lr2.debit_credit_flip.get(g.period):
            g.flags.append("debit balance shown as a liability" if g.nature in ("trade_payable", "loan_payable") else "negative asset: present as a liability")
            if g.status == "Agreed":
                g.status = "Presentation"
    ctx.shared["rpt_grid"] = rows
    return rows


def stale_fy_equals_q4(note: RPTNote, ledger: RPTLedger) -> list[tuple[RPTRow, str]]:
    """RPT note rows whose full-year column repeats the quarter column (a stale copy)."""
    out = []
    for row in note.rows:
        if row.section != "transactions" or row.hidden:
            continue
        for fy, q in (("CY", "Q4_CY"), ("PY", "Q4_PY")):
            if fy in row.values and q in row.values and row.values[fy] == row.values[q] and row.values[fy] != 0:
                tb = next((x for x in ledger.rows if similarity(x.counterparty, row.party) >= 90 and family(x.nature) == family(row.nature)), None)
                if tb is None or tb.values.get(fy) != tb.values.get(q):
                    out.append((row, fy))
    return out


def classification_switches(note: RPTNote) -> list[tuple[str, str, str, RPTRow, RPTRow]]:
    """(party, CY nature, PY nature, CY row, PY row): the same party's transaction re-classified between years."""
    out = []
    by_party: dict[str, list[RPTRow]] = {}
    for r in note.rows:
        if r.section == "transactions" and not r.hidden:
            by_party.setdefault(normalise(r.party), []).append(r)
    for _p, rows in by_party.items():
        cy_only = [r for r in rows if r.values.get("CY") and not r.values.get("PY")]
        py_only = [r for r in rows if r.values.get("PY") and not r.values.get("CY")]
        for a in cy_only:
            for b in py_only:
                if family(a.nature) != family(b.nature) and {a.nature, b.nature} <= {"services_received", "reimbursement", "remuneration", "services_rendered", "purchases", "sales", "rent"}:
                    out.append((a.party, a.nature, b.nature, a, b))
    return out


@dataclass
class Confirmation:
    party: str
    nature: str
    balance: Decimal  # per the counterparty's books, presented from this entity's side, statement unit
    as_at: date | None = None


def reconcile_confirmations(grid: list[GridRow], confirmations: list[Confirmation], *, period: str = "CY") -> list[dict[str, Any]]:
    """RPT-06: intercompany confirmations vs this entity's books (TB, else the RPT note)."""
    out = []
    parties = sorted({g.counterparty for g in grid})
    matched_keys: set[tuple[str, str]] = set()
    for conf in confirmations:
        cp, _s = best_match(conf.party, parties, threshold=88)
        fam = family(conf.nature)
        g = next((x for x in grid if x.counterparty == cp and x.nature == fam and x.period == period), None) if cp else None
        ours = (g.tb_value if g and g.tb_value is not None else (g.rpt_value if g else None))
        diff: Decimal | None = None if ours is None else conf.balance - ours
        status = "one-sided (not in our books)" if diff is None else ("agreed" if abs(diff) <= TOL else "difference")
        if cp:
            matched_keys.add((cp, fam))
        out.append({"party": conf.party, "matched_party": cp, "nature": fam, "confirmed": str(conf.balance),
                    "ours": str(ours) if ours is not None else None, "difference": str(diff) if diff is not None else None,
                    "status": status, "as_at": conf.as_at.isoformat() if conf.as_at else None})
    for g in grid:
        if g.period == period and g.nature in ("trade_payable", "trade_receivable", "loan_payable", "loan_receivable", "recoverable") \
                and (g.counterparty, g.nature) not in matched_keys and (g.tb_value or g.rpt_value):
            out.append({"party": g.counterparty, "matched_party": g.counterparty, "nature": g.nature, "confirmed": None,
                        "ours": str(g.tb_value if g.tb_value is not None else g.rpt_value), "difference": None,
                        "status": "one-sided (no confirmation received)", "as_at": None})
    return out
