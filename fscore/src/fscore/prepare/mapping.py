"""Mapping library (E-DATA-02): TB account -> mapping key -> Schedule III line -> note.

A mapping version is data: ``{account code: key}`` plus metadata. Versions are
diffed year on year so new / changed accounts are reviewed before use.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

# Schedule III Division II chart used by the house templates (version chart/1).
# sign: +1 = presented as the TB debit balance (assets, expenses); -1 = presented negated
CHART_VERSION = "chart-ind-as-div2/1"


@dataclass(frozen=True)
class ChartLine:
    key: str
    caption: str
    sign: int
    statement: str  # bs | pl | oci
    note: str


_C: list[ChartLine] = []


def _add(key: str, caption: str, sign: int, statement: str, note: str) -> None:
    _C.append(ChartLine(key, caption, sign, statement, note))


# --- assets
for cls in ("computers", "furniture", "office_equipment"):
    _add(f"ppe.gross.{cls}", f"Gross block – {cls.replace('_', ' ')}", 1, "bs", "ppe")
    _add(f"ppe.accdep.{cls}", f"Accumulated depreciation – {cls.replace('_', ' ')}", 1, "bs", "ppe")
_add("cwip", "Capital work-in-progress", 1, "bs", "ppe")
_add("rou.gross.buildings", "Right-of-use assets – buildings (gross)", 1, "bs", "rou")
_add("rou.accdep.buildings", "Right-of-use assets – accumulated depreciation", 1, "bs", "rou")
_add("intangibles.gross.software", "Computer software (gross)", 1, "bs", "intangibles")
_add("intangibles.accamort.software", "Computer software – accumulated amortisation", 1, "bs", "intangibles")
_add("inv.nc.subsidiary", "Investment in subsidiary (equity, at cost)", 1, "bs", "investments")
_add("ofa.nc.security_deposits", "Security deposits", 1, "bs", "ofa")
_add("dta", "Deferred tax assets (net)", 1, "bs", "dta")
_add("onca.capital_advances", "Capital advances", 1, "bs", "other_assets")
_add("tr.good.third_party", "Trade receivables – considered good (third parties)", 1, "bs", "tr")
_add("tr.good.related", "Trade receivables – considered good (related parties)", 1, "bs", "tr")
_add("tr.impaired", "Trade receivables – credit impaired", 1, "bs", "tr")
_add("tr.allowance", "Allowance for expected credit loss", 1, "bs", "tr")
_add("cash.bank_current", "Balances with banks – in current accounts", 1, "bs", "cash")
_add("cash.on_hand", "Cash on hand", 1, "bs", "cash")
_add("ofa.c.interest_accrued", "Interest accrued on deposits", 1, "bs", "ofa")
_add("ofa.c.unbilled", "Unbilled revenue", 1, "bs", "ofa")
_add("ofa.c.recoverable_group", "Recoverable from group companies", 1, "bs", "ofa")
_add("oca.prepaid", "Prepaid expenses", 1, "bs", "other_assets")
_add("oca.govt_balances", "Balances with government authorities", 1, "bs", "other_assets")
_add("oca.advances_suppliers", "Advances to suppliers", 1, "bs", "other_assets")
# --- equity and liabilities (credit balances, presented negated)
_add("eq.share_capital", "Equity share capital", -1, "bs", "share_capital")
_add("oe.securities_premium", "Securities premium", -1, "bs", "other_equity")
_add("oe.retained_earnings", "Retained earnings (opening)", -1, "bs", "other_equity")
_add("borr.nc.term_loan_bank", "Term loan from bank (secured)", -1, "bs", "borrowings")
_add("borr.nc.loan_holding", "Loan from holding company (unsecured)", -1, "bs", "borrowings")
_add("borr.c.current_maturities", "Current maturities of long-term borrowings", -1, "bs", "borrowings")
_add("lease.nc", "Lease liabilities – non-current", -1, "bs", "lease")
_add("lease.c", "Lease liabilities – current", -1, "bs", "lease")
_add("prov.nc.gratuity", "Provision for gratuity – non-current", -1, "bs", "provisions")
_add("prov.c.gratuity", "Provision for gratuity – current", -1, "bs", "provisions")
_add("prov.c.compensated_absences", "Provision for compensated absences", -1, "bs", "provisions")
_add("tp.msme", "Trade payables – micro and small enterprises", -1, "bs", "tp")
_add("tp.others", "Trade payables – others", -1, "bs", "tp")
_add("tp.related", "Trade payables – related parties", -1, "bs", "tp")
_add("ofl.employee_payable", "Employee benefits payable", -1, "bs", "ofl")
_add("ofl.interest_accrued", "Interest accrued on borrowings", -1, "bs", "ofl")
_add("ofl.capital_creditors", "Capital creditors", -1, "bs", "ofl")
_add("ocl.statutory_dues", "Statutory dues payable", -1, "bs", "ocl")
_add("ocl.advance_customers", "Advance from customers", -1, "bs", "ocl")
_add("ctl.net", "Current tax liabilities (net)", -1, "bs", "ctl")
# --- profit and loss
_add("rev.services_domestic", "Sale of services – domestic", -1, "pl", "revenue")
_add("rev.services_export", "Sale of services – export", -1, "pl", "revenue")
_add("rev.related_support", "Support services to group companies", -1, "pl", "revenue")
_add("oi.interest_deposits", "Interest income on bank deposits", -1, "pl", "other_income")
_add("oi.misc", "Miscellaneous income", -1, "pl", "other_income")
_add("emp.salaries", "Salaries, wages and bonus", 1, "pl", "employee")
_add("emp.pf", "Contribution to provident and other funds", 1, "pl", "employee")
_add("emp.gratuity", "Gratuity expense", 1, "pl", "employee")
_add("emp.welfare", "Staff welfare expenses", 1, "pl", "employee")
_add("fin.interest_bank", "Interest on term loan", 1, "pl", "finance")
_add("fin.interest_holding", "Interest on loan from holding company", 1, "pl", "finance")
_add("fin.interest_lease", "Interest on lease liabilities", 1, "pl", "finance")
_add("dep.ppe", "Depreciation on property, plant and equipment", 1, "pl", "depreciation")
_add("dep.rou", "Depreciation on right-of-use assets", 1, "pl", "depreciation")
_add("dep.intangibles", "Amortisation of intangible assets", 1, "pl", "depreciation")
_add("oth.legal_professional", "Legal and professional fees", 1, "pl", "other_expenses")
_add("oth.travel", "Travelling and conveyance", 1, "pl", "other_expenses")
_add("oth.communication", "Communication expenses", 1, "pl", "other_expenses")
_add("oth.software", "Software subscription charges", 1, "pl", "other_expenses")
_add("oth.ecl", "Provision for expected credit loss", 1, "pl", "other_expenses")
_add("oth.auditor", "Payment to auditors", 1, "pl", "other_expenses")
_add("oth.support_services_group", "Support services from group companies", 1, "pl", "other_expenses")
_add("oth.misc", "Miscellaneous expenses", 1, "pl", "other_expenses")
_add("tax.current", "Current tax", 1, "pl", "tax")
_add("tax.deferred", "Deferred tax", 1, "pl", "tax")
_add("oci.remeasurement", "Remeasurement of defined benefit plans", 1, "oci", "oci")
_add("oci.tax", "Income tax relating to items that will not be reclassified", 1, "oci", "oci")

CHART: dict[str, ChartLine] = {c.key: c for c in _C}

ASSET_PREFIXES = ("ppe.", "cwip", "rou.", "intangibles.", "inv.", "ofa.", "dta", "onca.", "tr.", "cash.", "oca.")
LIABILITY_PREFIXES = ("eq.", "oe.", "borr.", "lease.", "prov.", "tp.", "ofl.", "ocl.", "ctl.")


def presentation_sign(key: str) -> int:
    line = CHART.get(key)
    return line.sign if line else 1


# keyword rules used to *suggest* a mapping for new accounts (never applied silently)
_SUGGEST: list[tuple[str, str]] = [
    (r"accumulated depreciation.*computer", "ppe.accdep.computers"),
    (r"accumulated depreciation.*furniture", "ppe.accdep.furniture"),
    (r"accumulated depreciation.*(office|equipment)", "ppe.accdep.office_equipment"),
    (r"computer", "ppe.gross.computers"),
    (r"furniture", "ppe.gross.furniture"),
    (r"office equipment", "ppe.gross.office_equipment"),
    (r"capital work", "cwip"),
    (r"right.of.use.*(depreciation|accumulated)", "rou.accdep.buildings"),
    (r"right.of.use", "rou.gross.buildings"),
    (r"(software|intangible).*(amortis|amortiz)", "intangibles.accamort.software"),
    (r"intangible|software licen", "intangibles.gross.software"),
    (r"security deposit", "ofa.nc.security_deposits"),
    (r"deferred tax", "dta"),
    (r"capital advance", "onca.capital_advances"),
    (r"(expected credit|doubtful).*(allowance|provision)", "tr.allowance"),
    (r"(intercompany|inter company|related part).*(receivable|debtor)", "tr.good.related"),
    (r"trade receivable|sundry debtor|accounts receivable", "tr.good.third_party"),
    (r"cash on hand|petty cash", "cash.on_hand"),
    (r"bank", "cash.bank_current"),
    (r"recoverable from group|reimbursement receivable", "ofa.c.recoverable_group"),
    (r"unbilled", "ofa.c.unbilled"),
    (r"interest accrued.*deposit", "ofa.c.interest_accrued"),
    (r"prepaid", "oca.prepaid"),
    (r"gst|input tax|government", "oca.govt_balances"),
    (r"advance to (supplier|vendor)", "oca.advances_suppliers"),
    (r"share capital", "eq.share_capital"),
    (r"securities premium|share premium", "oe.securities_premium"),
    (r"retained earnings|surplus|profit and loss", "oe.retained_earnings"),
    (r"term loan", "borr.nc.term_loan_bank"),
    (r"loan from (holding|parent)", "borr.nc.loan_holding"),
    (r"current maturit", "borr.c.current_maturities"),
    (r"lease liabilit.*current", "lease.c"),
    (r"lease liabilit", "lease.nc"),
    (r"gratuity.*current", "prov.c.gratuity"),
    (r"gratuity", "prov.nc.gratuity"),
    (r"compensated absence|leave encashment", "prov.c.compensated_absences"),
    (r"msme|micro and small", "tp.msme"),
    (r"(intercompany|inter company|related part).*(payable|creditor)", "tp.related"),
    (r"trade payable|sundry creditor|accounts payable", "tp.others"),
    (r"salary payable|employee.*payable", "ofl.employee_payable"),
    (r"interest accrued", "ofl.interest_accrued"),
    (r"capital creditor", "ofl.capital_creditors"),
    (r"tds|statutory|pf payable|esi payable", "ocl.statutory_dues"),
    (r"advance from customer|deferred revenue|contract liabilit", "ocl.advance_customers"),
    (r"provision for tax|income tax payable", "ctl.net"),
    (r"(intercompany|inter company|ic ).*(support|cost plus|pushback)", "rev.related_support"),
    (r"export", "rev.services_export"),
    (r"revenue|sale of services|income from services", "rev.services_domestic"),
    (r"interest income", "oi.interest_deposits"),
    (r"other income|miscellaneous income", "oi.misc"),
    (r"salar|wages|bonus", "emp.salaries"),
    (r"provident|esic|superannuation", "emp.pf"),
    (r"gratuity expense", "emp.gratuity"),
    (r"staff welfare", "emp.welfare"),
    (r"interest.*lease", "fin.interest_lease"),
    (r"interest.*(holding|parent|related)", "fin.interest_holding"),
    (r"interest", "fin.interest_bank"),
    (r"depreciation.*right", "dep.rou"),
    (r"amortis|amortiz", "dep.intangibles"),
    (r"depreciation", "dep.ppe"),
    (r"legal|professional", "oth.legal_professional"),
    (r"travel|conveyance", "oth.travel"),
    (r"communication|telephone|internet", "oth.communication"),
    (r"subscription", "oth.software"),
    (r"expected credit loss|bad debt|doubtful", "oth.ecl"),
    (r"audit", "oth.auditor"),
    (r"(intercompany|group).*(service|charge)", "oth.support_services_group"),
    (r"current tax", "tax.current"),
    (r"deferred tax expense|deferred tax charge", "tax.deferred"),
    (r"remeasurement", "oci.remeasurement"),
    (r"miscellaneous|sundry expense|general expense", "oth.misc"),
]


def suggest_key(name: str, levels: list[str] | None = None) -> str | None:
    text = " ".join([name, *(levels or [])]).lower()
    for pattern, key in _SUGGEST:
        if re.search(pattern, text):
            return key
    return None


class MappingDiff(BaseModel):
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    changed: list[tuple[str, str, str]] = Field(default_factory=list)  # code, old key, new key


class MappingVersion(BaseModel):
    client: str = ""
    name: str = "default"
    version: int = 1
    chart_version: str = CHART_VERSION
    rows: dict[str, str] = Field(default_factory=dict)  # account code -> key
    approved_by: str | None = None

    def key_for(self, code: str) -> str | None:
        return self.rows.get(code)

    @property
    def sha256(self) -> str:
        body = json.dumps({"chart": self.chart_version, "rows": self.rows}, sort_keys=True)
        return hashlib.sha256(body.encode()).hexdigest()

    def diff(self, previous: MappingVersion | None) -> MappingDiff:
        if previous is None:
            return MappingDiff(added=sorted(self.rows))
        prev = previous.rows
        return MappingDiff(
            added=sorted(set(self.rows) - set(prev)),
            removed=sorted(set(prev) - set(self.rows)),
            changed=sorted((c, prev[c], k) for c, k in self.rows.items() if c in prev and prev[c] != k),
        )

    def unknown_keys(self) -> list[str]:
        return sorted({k for k in self.rows.values() if k not in CHART})

    @classmethod
    def from_tb(cls, tb: Any, *, client: str = "", suggest: bool = True) -> MappingVersion:
        rows: dict[str, str] = {}
        for a in tb.accounts:
            k = a.key or (suggest_key(a.name, a.levels) if suggest else None)
            if k:
                rows[a.code] = k
        return cls(client=client, rows=rows)


@dataclass
class MappingLibrary:
    """In-memory library; the web platform persists versions in mapping_version."""

    versions: list[MappingVersion] = field(default_factory=list)

    def latest(self, client: str, name: str = "default") -> MappingVersion | None:
        vs = [v for v in self.versions if v.client == client and v.name == name]
        return max(vs, key=lambda v: v.version) if vs else None

    def add(self, mv: MappingVersion) -> MappingVersion:
        prev = self.latest(mv.client, mv.name)
        mv.version = (prev.version + 1) if prev else 1
        self.versions.append(mv)
        return mv
