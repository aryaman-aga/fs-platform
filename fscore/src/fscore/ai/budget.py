"""Monthly AI budgets per firm and per engagement (E-AI-04).

Spend is checked *before* a call with a cost estimate and recorded *after*
with the provider's reported usage. Crossing 80 % raises an alert flag;
a call that would exceed a limit is refused. The web platform may persist
the ledger in its database; the CLI uses the optional JSON file store.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, Field

ALERT_FRACTION = Decimal("0.80")


class BudgetLimits(BaseModel):
    firm_monthly_usd: Decimal | None = None
    engagement_monthly_usd: Decimal | None = None
    per_engagement_overrides: dict[str, Decimal] = Field(default_factory=dict)


class BudgetDecision(BaseModel):
    allowed: bool
    month: str
    firm_spent_usd: Decimal
    engagement_spent_usd: Decimal
    estimate_usd: Decimal
    firm_fraction: Decimal | None = None
    engagement_fraction: Decimal | None = None
    alert: bool = False  # at or above 80 % of a limit after this call
    reason: str = ""


def month_key(now: datetime | None = None) -> str:
    t = now or datetime.now(UTC)
    return f"{t.year:04d}-{t.month:02d}"


class BudgetLedger:
    def __init__(self, limits: BudgetLimits | None = None, *, store: Path | None = None) -> None:
        self.limits = limits or BudgetLimits()
        self.store = store
        self._lock = threading.Lock()
        self._spend: dict[str, dict[str, Decimal]] = {}  # month -> scope -> usd ("firm", "eng:<id>")
        if store is not None and store.exists():
            raw = json.loads(store.read_text(encoding="utf-8"))
            self._spend = {m: {k: Decimal(v) for k, v in scopes.items()} for m, scopes in raw.items()}

    def _save(self) -> None:
        if self.store is None:
            return
        data = {m: {k: str(v) for k, v in scopes.items()} for m, scopes in self._spend.items()}
        tmp = self.store.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.store)

    def spent(self, scope: str, month: str | None = None) -> Decimal:
        return self._spend.get(month or month_key(), {}).get(scope, Decimal(0))

    def _eng_limit(self, engagement: str | None) -> Decimal | None:
        if engagement and engagement in self.limits.per_engagement_overrides:
            return self.limits.per_engagement_overrides[engagement]
        return self.limits.engagement_monthly_usd

    def check(self, engagement: str | None, estimate_usd: Decimal, *, now: datetime | None = None) -> BudgetDecision:
        m = month_key(now)
        firm = self.spent("firm", m)
        eng = self.spent(f"eng:{engagement}", m) if engagement else Decimal(0)
        allowed, reason, alert = True, "", False
        ff = ef = None
        if self.limits.firm_monthly_usd is not None and self.limits.firm_monthly_usd > 0:
            ff = (firm + estimate_usd) / self.limits.firm_monthly_usd
            if firm + estimate_usd > self.limits.firm_monthly_usd:
                allowed, reason = False, "firm monthly AI budget would be exceeded"
            alert = alert or ff >= ALERT_FRACTION
        el = self._eng_limit(engagement)
        if engagement and el is not None and el > 0:
            ef = (eng + estimate_usd) / el
            if eng + estimate_usd > el:
                allowed, reason = False, reason or "engagement monthly AI budget would be exceeded"
            alert = alert or ef >= ALERT_FRACTION
        return BudgetDecision(
            allowed=allowed,
            month=m,
            firm_spent_usd=firm,
            engagement_spent_usd=eng,
            estimate_usd=estimate_usd,
            firm_fraction=ff,
            engagement_fraction=ef,
            alert=alert,
            reason=reason,
        )

    def record(self, engagement: str | None, cost_usd: Decimal, *, now: datetime | None = None) -> None:
        m = month_key(now)
        with self._lock:
            scopes = self._spend.setdefault(m, {})
            scopes["firm"] = scopes.get("firm", Decimal(0)) + cost_usd
            if engagement:
                k = f"eng:{engagement}"
                scopes[k] = scopes.get(k, Decimal(0)) + cost_usd
            self._save()
