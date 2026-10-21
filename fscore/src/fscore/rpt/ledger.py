"""TB-driven related-party ledger (RPT-08): the source of truth for RPT reconciliation.

Related-party accounts are identified from the trial balance sheet itself
(Level 1–5 descriptions, account names, FS grouping): Intercompany / Inter
company / the group name / Related Parties / Receivables from related parties /
Recoverable from group companies, IC P&L lines, IC equity and investments.
Each account is mapped to (counterparty, nature) and its amounts are read for
every period the TB's own headers name (e.g. "Mar-26", "Qtr Mar 26", "FY 25-26").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any

from fscore.engine.periods import parse_period, scope_key
from fscore.rpt.names import extract_party_from_account, find_party_in_text, looks_like_party
from fscore.rpt.natures import ASSET_NATURES, EXPENSE_NATURES, INCOME_NATURES, KEY_NATURES, LIABILITY_NATURES

if TYPE_CHECKING:
    from fscore.checks.base import CheckContext

_RP_WORDS = re.compile(
    r"inter[\s-]*company|\bic\b|related\s+part|receivables?\s+from\s+related|recoverable\s+from\s+group|group\s+compan|due\s+(to|from)\s+group",
    re.I,
)
_FC_HINT = re.compile(r"\b(fc|usd|aud|gbp|aed|sgd|eur|functional)\b|\$|£|€", re.I)
_INR_HINT = re.compile(r"\b(inr|rs\.?|rupees)\b|₹", re.I)


@dataclass
class TBColumn:
    col: int
    header: str
    period: str | None  # scope key
    duration: bool  # True for flows (quarter / FY), False for balances / unknown
    currency: str  # inr | fc | unknown


@dataclass
class LedgerAccount:
    code: str
    name: str
    text: str  # name + levels + grouping, for matching
    key: str
    row: int
    counterparty: str | None
    nature: str
    kind: str  # bs | pl
    raw: dict[str, Decimal] = field(default_factory=dict)  # period -> TB amount (debit +), INR column
    raw_fc: dict[str, Decimal] = field(default_factory=dict)


@dataclass
class LedgerRow:
    counterparty: str
    nature: str
    kind: str
    values: dict[str, Decimal] = field(default_factory=dict)  # presented, statement unit, 2 dp
    fc_values: dict[str, Decimal] = field(default_factory=dict)
    gross_values: dict[str, Decimal] = field(default_factory=dict)  # sum of same-sign components (gross vs net)
    accounts: list[str] = field(default_factory=list)
    debit_credit_flip: dict[str, bool] = field(default_factory=dict)  # period -> balance on the "wrong" side


@dataclass
class RPTLedger:
    sheet: str | None
    columns: list[TBColumn] = field(default_factory=list)
    accounts: list[LedgerAccount] = field(default_factory=list)
    rows: list[LedgerRow] = field(default_factory=list)
    divisor: Decimal = Decimal(1)

    def get(self, counterparty: str, nature: str) -> LedgerRow | None:
        return next((r for r in self.rows if r.counterparty == counterparty and r.nature == nature), None)

    def counterparties(self) -> list[str]:
        return sorted({r.counterparty for r in self.rows})

    def as_dicts(self) -> list[dict[str, Any]]:
        return [
            {"counterparty": r.counterparty, "nature": r.nature, "kind": r.kind, "accounts": r.accounts,
             "values": {k: str(v) for k, v in r.values.items()}}
            for r in self.rows
        ]


def _find_header(ctx: CheckContext, sh: Any) -> tuple[int, dict[str, int], list[int], list[TBColumn]] | None:
    for row in range(1, min(sh.max_row, 40) + 1):
        texts = {}
        for col in range(1, sh.max_col + 1):
            c = sh.cell(row, col)
            if c is not None and isinstance(c.value, str) and c.value.strip():
                texts[col] = c.value.strip()
        if len(texts) < 3:
            continue
        low = {c: t.lower() for c, t in texts.items()}
        if not any(re.search(r"\baccount|ledger|\bgl\b", t) for t in low.values()):
            continue
        roles: dict[str, int] = {}
        levels: list[int] = []
        for c, t in low.items():
            if re.fullmatch(r"level\s*\d", t):
                levels.append(c)
            elif re.search(r"grouping|fs\s*line|mapping|schedule\s*iii|fs\s*head", t):
                roles.setdefault("grouping", c)
            elif re.search(r"counterparty|subsidiary|party|entity", t):
                roles.setdefault("counterparty", c)
            elif re.search(r"name|description|particulars", t):
                roles.setdefault("name", c)
            elif re.search(r"\baccount|\bcode|\bno\b|number|internal\s*id|ledger|\bgl\b", t):
                roles.setdefault("code", c)
        columns: list[TBColumn] = []
        for c, t in texts.items():
            if c in roles.values() or c in levels:
                continue
            above = sh.cell(row - 1, c) if row > 1 else None
            above_t = above.value if above is not None and isinstance(above.value, str) else ""
            lab = parse_period(t)
            if lab is None:
                continue
            key = scope_key(lab, ctx.config)
            hint = f"{t} {above_t}"
            currency = "fc" if _FC_HINT.search(hint) and not _INR_HINT.search(hint) else ("inr" if _INR_HINT.search(hint) else "unknown")
            columns.append(TBColumn(c, t, key, lab.months is not None and lab.kind == "duration", currency))
        if columns:
            return row, roles, sorted(levels), columns
    return None


def _dec(v: Any) -> Decimal:
    if v is None or isinstance(v, (str, bool)):
        return Decimal(0)
    from fscore.engine.display import to_decimal15

    return to_decimal15(v)


def _nature_for(key: str, text: str, amount: Decimal) -> tuple[str, str]:
    k = (key or "").lower()
    for prefix, nature, kind in KEY_NATURES:
        if k.startswith(prefix):
            return nature, kind
    t = text.lower()
    if "share capital" in t:
        return "equity_balance", "bs"
    if re.search(r"investment", t):
        return "investment_balance", "bs"
    if re.search(r"loan|borrowing", t):
        if re.search(r"interest", t):
            return ("interest_paid", "pl") if amount >= 0 else ("interest_received", "pl")
        return ("loan_payable", "bs") if amount <= 0 else ("loan_receivable", "bs")
    if re.search(r"interest", t):
        return ("interest_paid", "pl") if amount >= 0 else ("interest_received", "pl")
    if re.search(r"recoverable|reimburse", t):
        return "recoverable", "bs"
    if re.search(r"payable|creditor", t):
        return "trade_payable", "bs"
    if re.search(r"receivable|debtor", t):
        return "trade_receivable", "bs"
    if re.search(r"salary|remuneration|esop", t):
        return "remuneration", "pl"
    if re.search(r"revenue|income|sales|recovery|pushback|cost\s*plus", t) and amount <= 0:
        return "services_rendered", "pl"
    if re.search(r"expense|charges|cost|fees|services|travel|software", t):
        return "services_received", "pl"
    return ("other_balance", "bs")


def _detect_divisor(ctx: CheckContext, accounts_amounts: list[Decimal]) -> Decimal:
    """TB in rupees (divide by the unit) or already in the statement unit?"""
    total_assets = ctx.key_figures.get("total_assets", "CY")
    debits = sum((a for a in accounts_amounts if a > 0), Decimal(0))
    unit = Decimal(ctx.config.unit_divisor)
    if total_assets and total_assets > 0 and debits > 0:
        ratio = debits / total_assets
        if ratio > unit / 50:
            return unit
        if ratio < 50:
            return Decimal(1)
    return unit


def build_ledger(ctx: CheckContext, *, known_parties: list[str] | None = None) -> RPTLedger:
    """Build the related-party ledger from the TB sheet (cached in ``ctx.shared['rpt_ledger']``)."""
    if "rpt_ledger" in ctx.shared:
        return ctx.shared["rpt_ledger"]  # type: ignore[no-any-return]
    sname = ctx.statements.sheet("trial_balance")
    ledger = RPTLedger(sname)
    if not sname:
        ctx.shared["rpt_ledger"] = ledger
        return ledger
    sh = ctx.wb.sheet(sname)
    found = _find_header(ctx, sh)
    if found is None:
        ctx.shared["rpt_ledger"] = ledger
        return ledger
    hdr_row, roles, levels, columns = found
    ledger.columns = columns
    group_words = [w.lower() for w in ctx.config.rpt.group_names if w]
    parties = list(known_parties or [])
    for extra in (ctx.config.rpt.holding_company, ctx.config.rpt.ultimate_holding_company, *ctx.config.rpt.group_structure.keys(),
                  *ctx.config.rpt.group_structure.values()):
        if extra and extra not in parties:
            parties.append(extra)
    all_amounts: list[Decimal] = []
    inr_cols = [c for c in columns if c.currency != "fc"]
    fc_cols = [c for c in columns if c.currency == "fc"]
    for row in range(hdr_row + 1, sh.max_row + 1):
        def text_at(col: int | None, row: int = row) -> str:
            if col is None:
                return ""
            c = sh.cell(row, col)
            return str(c.value).strip() if c is not None and c.value is not None else ""

        code = text_at(roles.get("code"))
        name = text_at(roles.get("name")) or code
        if not code and not name:
            continue
        if re.match(r"^\s*(grand\s+)?total", name, re.I) or re.match(r"^\s*(grand\s+)?total", code, re.I):
            continue
        lvl = [text_at(c) for c in levels]
        grouping = text_at(roles.get("grouping"))
        cp_col = text_at(roles.get("counterparty"))
        text = " | ".join(x for x in [name, *lvl, grouping, cp_col] if x)
        for col in inr_cols:
            if col.period == "CY" and not col.duration:
                all_amounts.append(_dec(sh.value(row, col.col)))
        is_rp = bool(_RP_WORDS.search(text)) or any(w in text.lower() for w in group_words)
        if not is_rp and grouping.lower().startswith(("tr.good.related", "tp.related", "borr.nc.loan_holding", "ofa.c.recoverable_group", "rev.related", "oth.support_services_group", "fin.interest_holding")):
            is_rp = True
        # equity / investments are related-party items when the counterparty is a group company
        if not is_rp and re.search(r"share\s+capital|investment", text, re.I) and (cp_col or find_party_in_text(text, parties)):
            is_rp = True
        if not is_rp:
            continue
        counterparty = cp_col or find_party_in_text(text, parties) or extract_party_from_account(name)
        if counterparty is None:
            for t in reversed(lvl):
                if looks_like_party(t):
                    counterparty = t
                    break
        if counterparty is None and re.search(r"share\s+capital", text, re.I) and ctx.config.rpt.holding_company:
            counterparty = ctx.config.rpt.holding_company
        sample = _dec(sh.value(row, inr_cols[0].col)) if inr_cols else Decimal(0)
        nature, kind = _nature_for(grouping, text, sample)
        acc = LedgerAccount(code or name, name, text, grouping, row, counterparty, nature, kind)
        for col in inr_cols:
            if col.period is None:
                continue
            if kind == "bs" and col.duration:
                continue
            if kind == "pl" and not col.duration and any(c.duration and c.period == col.period for c in inr_cols):
                continue  # flows come from duration columns when the TB has them
            acc.raw[col.period] = acc.raw.get(col.period, Decimal(0)) + _dec(sh.value(row, col.col))
        for col in fc_cols:
            if col.period is None:
                continue
            acc.raw_fc[col.period] = acc.raw_fc.get(col.period, Decimal(0)) + _dec(sh.value(row, col.col))
        ledger.accounts.append(acc)
    ledger.divisor = _detect_divisor(ctx, all_amounts)
    # aggregate per counterparty × nature, presented sign, statement unit, 2 dp
    q = Decimal("0.01")
    for acc in ledger.accounts:
        cp = acc.counterparty or "Unidentified group company"
        row_ = ledger.get(cp, acc.nature)
        if row_ is None:
            row_ = LedgerRow(cp, acc.nature, acc.kind)
            ledger.rows.append(row_)
        sign = Decimal(-1) if acc.nature in LIABILITY_NATURES | INCOME_NATURES else Decimal(1)
        row_.accounts.append(acc.code)
        for period, amt in acc.raw.items():
            pres = amt * sign / ledger.divisor
            row_.values[period] = row_.values.get(period, Decimal(0)) + pres
            if pres > 0:
                row_.gross_values[period] = row_.gross_values.get(period, Decimal(0)) + pres
        for period, amt in acc.raw_fc.items():
            row_.fc_values[period] = row_.fc_values.get(period, Decimal(0)) + amt * sign / ledger.divisor
    for r in ledger.rows:
        for period in list(r.values):
            r.values[period] = r.values[period].quantize(q, rounding=ROUND_HALF_UP)
            r.gross_values[period] = r.gross_values.get(period, Decimal(0)).quantize(q, rounding=ROUND_HALF_UP)
            # a payable with a debit balance (or a receivable with a credit balance) has the wrong presentation
            if r.nature in LIABILITY_NATURES | ASSET_NATURES:
                r.debit_credit_flip[period] = r.values[period] < 0
        for period in list(r.fc_values):
            r.fc_values[period] = r.fc_values[period].quantize(q, rounding=ROUND_HALF_UP)
    _ = EXPENSE_NATURES
    ctx.shared["rpt_ledger"] = ledger
    return ledger
