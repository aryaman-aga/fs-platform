"""Trial balance model, loaders and the TB validation gate (E-DATA-01).

Convention: amounts are in the TB currency's base unit (rupees), debit positive,
credit negative. A balanced TB nets to zero per period (and per currency).
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from fscore.prepare.mapping import ASSET_PREFIXES, LIABILITY_PREFIXES, MappingVersion


@dataclass
class TBAccount:
    code: str
    name: str
    levels: list[str] = field(default_factory=lambda: ["", "", "", "", ""])
    key: str = ""  # mapping key (Schedule III line), "" when unmapped
    amounts: dict[str, Decimal] = field(default_factory=dict)  # period -> amount (debit +)
    fc_amounts: dict[str, Decimal] = field(default_factory=dict)
    counterparty: str | None = None

    def amount(self, period: str) -> Decimal:
        return self.amounts.get(period, Decimal(0))


@dataclass
class TrialBalance:
    entity: str
    currency: str = "INR"
    functional_currency: str | None = None
    periods: list[str] = field(default_factory=list)  # e.g. ["CY", "PY"]
    period_labels: dict[str, str] = field(default_factory=dict)  # "CY" -> "Mar-26"
    period_ends: dict[str, date] = field(default_factory=dict)
    accounts: list[TBAccount] = field(default_factory=list)
    fx_rates: dict[str, dict[str, Decimal]] = field(default_factory=dict)  # period -> {closing, average, historical}

    def total(self, period: str) -> Decimal:
        return sum((a.amount(period) for a in self.accounts), Decimal(0))

    def by_key(self, key: str, period: str) -> Decimal:
        return sum((a.amount(period) for a in self.accounts if a.key == key), Decimal(0))

    def keys(self) -> set[str]:
        return {a.key for a in self.accounts if a.key}

    def apply_mapping(self, mapping: MappingVersion) -> list[str]:
        """Assign mapping keys from a mapping version; returns unmapped account codes."""
        unmapped = []
        for a in self.accounts:
            k = mapping.key_for(a.code)
            if k:
                a.key = k
            elif not a.key:
                unmapped.append(a.code)
        return unmapped


class TBIssue(BaseModel):
    code: str  # TB-NET, TB-UNMAPPED, TB-SIGN, TB-PERIOD, TB-FX, TB-OPENING, TB-EMPTY
    severity: str  # block | warn
    message: str
    accounts: list[str] = Field(default_factory=list)


class TBValidationResult(BaseModel):
    ok: bool
    issues: list[TBIssue] = Field(default_factory=list)
    totals: dict[str, str] = Field(default_factory=dict)

    @property
    def blocking(self) -> list[TBIssue]:
        return [i for i in self.issues if i.severity == "block"]


def validate_tb(
    tb: TrialBalance,
    *,
    expected_period_ends: dict[str, date] | None = None,
    prior_closing: dict[str, Decimal] | None = None,
    presentation_currency: str = "INR",
    tolerance: Decimal = Decimal("1"),
) -> TBValidationResult:
    """E-DATA-01 gate: blocks preparation/review until the TB is fit for use."""
    issues: list[TBIssue] = []
    totals: dict[str, str] = {}
    if not tb.accounts:
        issues.append(TBIssue(code="TB-EMPTY", severity="block", message="The trial balance has no accounts."))
    for p in tb.periods:
        t = tb.total(p)
        totals[p] = str(t)
        if abs(t) > tolerance:
            issues.append(TBIssue(code="TB-NET", severity="block", message=f"TB for {tb.period_labels.get(p, p)} does not net to zero (difference {t:,.2f})."))
    unmapped = [a.code for a in tb.accounts if not a.key and any(a.amount(p) for p in tb.periods)]
    if unmapped:
        issues.append(TBIssue(code="TB-UNMAPPED", severity="block", message=f"{len(unmapped)} account(s) with balances are not mapped.", accounts=unmapped[:200]))
    sign_odd: list[str] = []
    for a in tb.accounts:
        cy = a.amount(tb.periods[0]) if tb.periods else Decimal(0)
        if not a.key or cy == 0:
            continue
        if a.key.startswith(ASSET_PREFIXES) and cy < 0 and not a.key.endswith((".allowance", ".accdep", ".accamort")):
            sign_odd.append(a.code)
        if a.key.startswith(LIABILITY_PREFIXES) and cy > 0:
            sign_odd.append(a.code)
    if sign_odd:
        issues.append(TBIssue(code="TB-SIGN", severity="warn", message=f"{len(sign_odd)} account(s) carry an unusual sign (e.g. an asset with a credit balance); check classification.", accounts=sign_odd[:200]))
    if expected_period_ends:
        for k, d in expected_period_ends.items():
            got = tb.period_ends.get(k)
            if got is not None and got != d:
                issues.append(TBIssue(code="TB-PERIOD", severity="block", message=f"Period {k} ends {got:%d-%b-%Y} but the engagement expects {d:%d-%b-%Y}."))
    fc = tb.functional_currency
    if fc and fc != presentation_currency:
        for p in tb.periods:
            rates = tb.fx_rates.get(p, {})
            missing = [r for r in ("closing", "average", "historical") if r not in rates]
            if missing:
                issues.append(TBIssue(code="TB-FX", severity="block", message=f"FX rates {', '.join(missing)} missing for {tb.period_labels.get(p, p)} ({fc}→{presentation_currency})."))
    if prior_closing:
        diffs = []
        if "PY" in tb.periods:
            for a in tb.accounts:
                if a.code in prior_closing and abs(a.amount("PY") - prior_closing[a.code]) > tolerance:
                    diffs.append(a.code)
        if diffs:
            issues.append(TBIssue(code="TB-OPENING", severity="block", message=f"{len(diffs)} PY balance(s) differ from the prior signed closing balances.", accounts=diffs[:200]))
    ok = not any(i.severity == "block" for i in issues)
    return TBValidationResult(ok=ok, issues=issues, totals=totals)


# --------------------------------------------------------------------------- loaders

_HEADER_ALIASES = {
    "code": ("account", "account no", "account number", "account code", "acct", "gl code", "ledger code", "internal id"),
    "name": ("account name", "name", "ledger", "ledger name", "description", "account description"),
    "key": ("fs grouping", "grouping", "mapping", "fs line", "mapping key", "schedule iii"),
}


def _dec(v: Any) -> Decimal:
    if v is None or v == "":
        return Decimal(0)
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    s = str(v).strip().replace(",", "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    if s.endswith((" Cr", " CR")):
        neg, s = True, s[:-3]
    elif s.endswith((" Dr", " DR")):
        s = s[:-3]
    try:
        d = Decimal(s) if s not in ("", "-") else Decimal(0)
    except InvalidOperation as exc:
        raise ValueError(f"not an amount: {v!r}") from exc
    return -d if neg else d


def load_tb_rows(rows: list[list[Any]], *, entity: str = "Entity", periods: dict[str, str] | None = None) -> TrialBalance:
    """Build a TB from tabular rows (first non-empty row containing 'account' is the header).

    ``periods`` maps period keys to header labels, e.g. {"CY": "Mar-26", "PY": "Mar-25"}.
    Without it, the right-most two numeric columns are taken as CY and PY.
    """
    hdr_idx = None
    for i, r in enumerate(rows[:50]):
        cells = [str(x).strip().lower() if x is not None else "" for x in r]
        if any(c in _HEADER_ALIASES["code"] or c.startswith("account") for c in cells):
            hdr_idx = i
            break
    if hdr_idx is None:
        raise ValueError("cannot find the TB header row (a column named 'Account')")
    header = [str(x).strip() if x is not None else "" for x in rows[hdr_idx]]
    low = [h.lower() for h in header]

    def find(kind: str) -> int | None:
        for j, h in enumerate(low):
            if h in _HEADER_ALIASES[kind]:
                return j
        return None

    ci, ni, ki = find("code"), find("name"), find("key")
    if ci is None:
        ci = next(j for j, h in enumerate(low) if h.startswith("account"))
    level_cols = [j for j, h in enumerate(low) if re.fullmatch(r"level\s*\d", h)]
    tb = TrialBalance(entity=entity)
    if periods:
        pcols = {}
        for pk, label in periods.items():
            j = next((j for j, h in enumerate(header) if h.strip().lower() == label.strip().lower()), None)
            if j is None:
                raise ValueError(f"period column {label!r} not found in TB header")
            pcols[pk] = j
            tb.periods.append(pk)
            tb.period_labels[pk] = label
    else:
        numeric = []
        for j in range(len(header)):
            if j in (ci, ni, ki) or j in level_cols:
                continue
            vals = [r[j] for r in rows[hdr_idx + 1 : hdr_idx + 30] if j < len(r)]
            if vals and sum(1 for v in vals if isinstance(v, (int, float)) or (isinstance(v, str) and re.fullmatch(r"[\s\-()\d,.]+", v or "x"))) >= max(1, len(vals) // 2):
                numeric.append(j)
        if len(numeric) < 1:
            raise ValueError("no amount columns found in the TB")
        chosen = numeric[-2:] if len(numeric) >= 2 else numeric
        pcols = {}
        for pk, j in zip(["CY", "PY"], chosen, strict=False):
            pcols[pk] = j
            tb.periods.append(pk)
            tb.period_labels[pk] = header[j]
    for r in rows[hdr_idx + 1 :]:
        if ci >= len(r) or r[ci] in (None, ""):
            continue
        code = str(r[ci]).strip()
        if code.lower().startswith("total"):
            continue
        acct = TBAccount(code=code, name=str(r[ni]).strip() if ni is not None and ni < len(r) and r[ni] is not None else code)
        acct.levels = [str(r[j]).strip() if j < len(r) and r[j] is not None else "" for j in level_cols][:5]
        while len(acct.levels) < 5:
            acct.levels.append("")
        if ki is not None and ki < len(r) and r[ki]:
            acct.key = str(r[ki]).strip()
        for pk, j in pcols.items():
            acct.amounts[pk] = _dec(r[j] if j < len(r) else None)
        tb.accounts.append(acct)
    return tb


def load_tb(path: str | Path, *, sheet: str | None = None, entity: str = "Entity", periods: dict[str, str] | None = None) -> TrialBalance:
    p = Path(path)
    if p.suffix.lower() == ".csv":
        with p.open(newline="", encoding="utf-8-sig") as fh:
            rows: list[list[Any]] = [list(r) for r in csv.reader(fh)]
        return load_tb_rows(rows, entity=entity, periods=periods)
    from fscore.io.xlsx_reader import Workbook

    with Workbook.open(p) as wb:
        sh = wb.get_sheet(sheet) if sheet else next((s for s in wb.sheets if re.match(r"(ns_)?tb|trial", s.name, re.I)), wb.sheets[0])
        if sh is None:
            raise ValueError(f"sheet {sheet!r} not found")
        grid: list[list[Any]] = [[None] * sh.max_col for _ in range(sh.max_row)]
        for c in sh.iter_cells():
            if c.row <= sh.max_row and c.col <= sh.max_col:
                grid[c.row - 1][c.col - 1] = c.value
    return load_tb_rows(grid, entity=entity, periods=periods)
