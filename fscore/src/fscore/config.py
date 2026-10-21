"""Engagement configuration (02 §8), loaded from YAML per client and year.

Every field has a safe default so a workbook can be checked with no
configuration at all; `fscheck init` writes a starter file with the
auto-detected values for the user to confirm.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

Units = Literal["rupees", "thousands", "lakhs", "millions", "crores"]

UNIT_DIVISORS: dict[str, int] = {
    "rupees": 1,
    "thousands": 1_000,
    "lakhs": 100_000,
    "millions": 1_000_000,
    "crores": 10_000_000,
}


class _Cfg(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PrintSheetSelection(_Cfg):
    """Which sheets are 'print sheets' (02 §2.1). Default: all visible sheets except TB/backups."""

    first: str | None = None
    last: str | None = None
    include_hidden: bool = False
    all_visible_except: list[str] = Field(
        default_factory=lambda: ["NS_TB*", "TB*", "Trial*", "*Backup*", "*Working*", "FAR*", "Ageing*", "Aging*", "*Ledger*", "Dep*Sch*", "Lead*"]
    )
    explicit: list[str] = Field(default_factory=list)
    use_tab_group: bool = True  # when 2+ visible sheets are grouped (tab-selected), they are the print block


class StatementSheets(_Cfg):
    balance_sheet: str | None = None
    profit_loss: str | None = None
    cash_flow: str | None = None
    soce: str | None = None
    rpt: str | None = None
    going_concern: str | None = None
    ratios: str | None = None
    trial_balance: str | None = None
    share_capital: str | None = None


class BalancingTarget(_Cfg):
    sheet: str | None = None
    caption: str  # "A|B" alternatives, matched case-insensitively against row captions


class BalancingPolicy(_Cfg):
    balance_sheet: BalancingTarget = Field(
        default_factory=lambda: BalancingTarget(caption=r"^\s*other expenses\s*$|^\s*miscellaneous expenses\s*$")
    )
    cash_flow: BalancingTarget = Field(
        default_factory=lambda: BalancingTarget(caption=r"increase in other current liabilit|other current liabilit")
    )


class FixPolicy(_Cfg):
    casting: Literal["totals_only", "line_adjust"] = "totals_only"
    previous_year: Literal["line_adjust", "totals_only"] = "totals_only"
    plug_threshold: Decimal = Decimal("0.99")  # PLUG-01 when |c| < this
    balancing_threshold: Decimal = Decimal("0.20")  # C3: only rounding-sized gaps are balanced
    balancing: BalancingPolicy = Field(default_factory=BalancingPolicy)
    max_passes: int = 15
    xref_rounding_tolerance: Decimal = Decimal("0.05")


class PeriodSpec(_Cfg):
    """A reporting period in scope (MULTI-01). Labels drive column identification."""

    key: str  # "CY", "PY", "Q4_CY", "Q4_PY", "FY_CY", "FY_PY"
    end: date
    months: int = 12  # 3 for a quarter, 12 for a year


class AIConfig(_Cfg):
    provider: Literal["none", "anthropic", "bedrock", "vertex", "foundry", "local", "gateway", "mock"] = "none"
    model: str = "claude-opus-5-5"
    region: str | None = None  # required for every external provider (no default by design)
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    require_approval: bool = True
    redact: bool = True
    retain_payloads: Literal["hash_only", "full"] = "hash_only"
    scale_amounts: bool = False
    endpoint: str | None = None  # local / gateway providers
    max_tokens: int = 16000


class RPTConfig(_Cfg):
    group_names: list[str] = Field(default_factory=list)  # e.g. ["Group B"] — words that mark intercompany accounts
    tds_rate: Decimal = Decimal("0.10")
    loan_tolerance: Decimal = Decimal("0.05")
    holding_company: str | None = None
    ultimate_holding_company: str | None = None
    group_structure: dict[str, str] = Field(default_factory=dict)  # entity -> parent


class RenderConfig(_Cfg):
    """Excel → PDF settings (02 §10A)."""

    engine: Literal["auto", "excel", "libreoffice", "native"] = "auto"
    allow_native_final: bool = False  # final PDFs need Excel or LibreOffice unless the firm allows the native renderer
    combined: bool = False
    separate_notes: bool = False


class EngagementConfig(_Cfg):
    client: str = "Client"
    entity: str | None = None
    year_end: date = date(2026, 3, 31)
    period_type: Literal["annual", "interim", "spfs"] = "annual"
    units: Units = "lakhs"
    currency: str = "INR"
    functional_currency: str | None = None
    standalone: bool = True
    periods_in_scope: list[PeriodSpec] = Field(default_factory=list)
    print_sheets: PrintSheetSelection = Field(default_factory=PrintSheetSelection)
    statements: StatementSheets = Field(default_factory=StatementSheets)
    policy: FixPolicy = Field(default_factory=FixPolicy)
    prior_year_signed: str | None = None
    signatories: str | None = None
    xref_map: str | None = None
    signing_window_days: int = 365
    ai: AIConfig = Field(default_factory=AIConfig)
    rpt: RPTConfig = Field(default_factory=RPTConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)
    disabled_rules: list[str] = Field(default_factory=list)

    @field_validator("periods_in_scope")
    @classmethod
    def _unique_keys(cls, v: list[PeriodSpec]) -> list[PeriodSpec]:
        keys = [p.key for p in v]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate period keys in periods_in_scope")
        return v

    def scope(self) -> list[PeriodSpec]:
        """Periods in scope; defaults to CY and PY year ends (and Q4 for interim/SPFS)."""
        if self.periods_in_scope:
            return list(self.periods_in_scope)
        py_end = date(self.year_end.year - 1, self.year_end.month, self.year_end.day)
        out = [PeriodSpec(key="CY", end=self.year_end, months=12), PeriodSpec(key="PY", end=py_end, months=12)]
        if self.period_type in ("interim", "spfs"):
            out = [
                PeriodSpec(key="Q4_CY", end=self.year_end, months=3),
                PeriodSpec(key="Q4_PY", end=py_end, months=3),
                PeriodSpec(key="CY", end=self.year_end, months=12),
                PeriodSpec(key="PY", end=py_end, months=12),
            ]
        return out

    @property
    def unit_divisor(self) -> int:
        return UNIT_DIVISORS[self.units]

    def config_hash(self) -> str:
        body = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


def load_config(path: str | Path | None) -> EngagementConfig:
    if path is None:
        return EngagementConfig()
    p = Path(path)
    data: Any = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{p}: configuration must be a mapping")
    return EngagementConfig.model_validate(data)


def dump_config(cfg: EngagementConfig) -> str:
    return yaml.safe_dump(cfg.model_dump(mode="json", exclude_none=True), sort_keys=False, allow_unicode=True)
