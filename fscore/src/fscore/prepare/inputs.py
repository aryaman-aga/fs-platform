"""Inputs to Module P: entity facts, trial balance and supplementary (non-TB) schedules.

Amounts are in rupees (the TB currency's base unit). The same structures feed
the synthetic generator and real preparation runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from fscore.prepare.tb import TrialBalance

if TYPE_CHECKING:
    from fscore.synthetic.ledger import SyntheticBooks

D = Decimal
ZERO = Decimal(0)

UNIT_DIVISORS = {"rupees": 1, "thousands": 1_000, "lakhs": 100_000, "millions": 1_000_000, "crores": 10_000_000}
UNIT_LABELS = {"rupees": "₹", "thousands": "₹ thousands", "lakhs": "₹ lakhs", "millions": "₹ million", "crores": "₹ crores"}


@dataclass
class Signatory:
    name: str
    role: str
    din: str | None = None
    membership_no: str | None = None


@dataclass
class EntityInfo:
    name: str = "Entity Private Limited"
    short: str = "Entity"
    cin: str | None = None
    registration_label: str = "CIN"
    address: str = ""
    activity: str = "services"
    year_end: date = date(2026, 3, 31)
    py_end: date = date(2025, 3, 31)
    units: str = "lakhs"
    unit_label: str = "₹ lakhs"
    directors: list[Signatory] = field(default_factory=list)
    cfo: Signatory | None = None
    auditor_firm: str = ""
    auditor_frn: str = ""
    auditor_partner: Signatory | None = None
    place: str = ""
    signing_date: date = date(2026, 5, 15)
    holding: str = ""
    ultimate_holding: str = ""
    group_name: str = ""
    fellow_subsidiaries: tuple[str, ...] = ()
    subsidiary: str = ""
    functional_currency: str = "INR"
    presentation_currency: str = "INR"
    standalone: bool = True


@dataclass
class Roll:
    """Opening + movements = closing (rupees)."""

    opening: Decimal = ZERO
    additions: Decimal = ZERO
    disposals: Decimal = ZERO
    closing: Decimal = ZERO
    dep_opening: Decimal = ZERO
    dep_charge: Decimal = ZERO
    dep_disposals: Decimal = ZERO
    dep_closing: Decimal = ZERO
    transfers: Decimal = ZERO


@dataclass
class YearData:
    """Supplementary (non-TB) facts for one financial year (rupees)."""

    far: dict[str, Roll] = field(default_factory=dict)
    rou: Roll = field(default_factory=Roll)
    software: Roll = field(default_factory=Roll)
    cwip: Roll = field(default_factory=Roll)
    lease: dict[str, Decimal] = field(default_factory=dict)
    lease_maturity: dict[str, Decimal] = field(default_factory=dict)
    gratuity: dict[str, Decimal] = field(default_factory=dict)
    term_loan: dict[str, Decimal] = field(default_factory=dict)
    holding_loan: dict[str, Decimal] = field(default_factory=dict)
    shares: dict[str, Any] = field(default_factory=dict)
    tax: dict[str, Decimal] = field(default_factory=dict)
    ctl: dict[str, Decimal] = field(default_factory=dict)
    dta: dict[str, Decimal] = field(default_factory=dict)
    cash_flow: dict[str, Decimal] = field(default_factory=dict)
    ic: dict[str, Decimal] = field(default_factory=dict)
    receivable_ageing: dict[str, Decimal] = field(default_factory=dict)
    payable_ageing: dict[str, dict[str, Decimal]] = field(default_factory=dict)
    msme: dict[str, Decimal] = field(default_factory=dict)
    remuneration: Decimal = ZERO
    revenue_geo: dict[str, Decimal] = field(default_factory=dict)
    contingent: dict[str, Decimal] = field(default_factory=dict)
    commitments: Decimal = ZERO
    bs_open: dict[str, Decimal] = field(default_factory=dict)
    bs_close: dict[str, Decimal] = field(default_factory=dict)
    pl: dict[str, Decimal] = field(default_factory=dict)


@dataclass
class PrepareInputs:
    entity: EntityInfo
    tb: TrialBalance
    years: dict[str, YearData]  # "CY", "PY"
    opening: dict[str, Decimal]  # account balances at the start of PY (prior-year signed closing)
    units: str = "lakhs"
    layout: str = "annual"  # annual | spfs
    exact: bool = False
    cast_safe_totals: bool = False  # Module P on real data: totals add printed (rounded) components
    quarter_fraction: dict[str, Decimal] = field(default_factory=dict)
    fx: dict[str, dict[str, Decimal]] = field(default_factory=dict)
    flags: set[str] = field(default_factory=set)  # structural injections (synthetic tests only)
    counterparties: dict[str, str] = field(default_factory=dict)  # account code -> counterparty

    @property
    def divisor(self) -> int:
        return UNIT_DIVISORS[self.units]

    @classmethod
    def from_books(cls, books: SyntheticBooks, *, layout: str = "annual", flags: set[str] | None = None) -> PrepareInputs:
        return cls(
            entity=books.entity,
            tb=books.tb,
            years=books.years,
            opening=books.opening,
            units=books.units,
            layout=layout,
            exact=books.exact,
            quarter_fraction=dict(books.quarter_fraction),
            fx=dict(books.fx),
            flags=set(flags or ()),
            counterparties={a.code: a.counterparty for a in books.tb.accounts if a.counterparty},
        )
