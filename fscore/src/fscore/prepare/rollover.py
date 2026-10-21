"""Year-end rollover (E-PREP-01).

A new engagement takes its comparatives from the prior year's *signed*
figures — not the prior working file — and carries forward the mapping, note
structure and signatories (signatories are flagged for re-confirmation).
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, Field

from fscore.prepare.inputs import EntityInfo, Signatory
from fscore.prepare.mapping import MappingDiff, MappingVersion, presentation_sign
from fscore.prepare.tb import TrialBalance

D = Decimal


class PriorSigned(BaseModel):
    """Figures of the signed prior-year statements, by mapping key, in presentation units (2 dp)."""

    entity: str
    year_end: date
    units_divisor: int = 100_000
    figures: dict[str, Decimal] = Field(default_factory=dict)
    signatories: list[dict[str, str | None]] = Field(default_factory=list)
    mapping: MappingVersion | None = None
    source: str = "signed financial statements"


class ComparativeDifference(BaseModel):
    key: str
    signed: Decimal
    tb: Decimal
    difference: Decimal


class RolloverResult(BaseModel):
    ok: bool
    year_end: date
    differences: list[ComparativeDifference] = Field(default_factory=list)
    mapping: MappingVersion | None = None
    mapping_diff: MappingDiff | None = None
    signatories_to_reconfirm: list[dict[str, str | None]] = Field(default_factory=list)
    message: str = ""


def _q2(x: Decimal) -> Decimal:
    return x.quantize(D("0.01"), rounding=ROUND_HALF_UP)


def signed_figures_from_tb(tb: TrialBalance, period: str, *, divisor: int, entity: EntityInfo | None = None,
                           mapping: MappingVersion | None = None) -> PriorSigned:
    """Capture a final year's figures (as signed) from its closing TB, by mapping key."""
    figures: dict[str, Decimal] = {}
    for k in sorted(tb.keys()):
        if k.startswith("oe.retained_earnings"):
            continue  # reserves are signed as closing balances via the SOCE, not the opening TB balance
        figures[k] = _q2(presentation_sign(k) * tb.by_key(k, period) / divisor)
    sig = []
    if entity is not None:
        for s in [*entity.directors, *([entity.auditor_partner] if entity.auditor_partner else [])]:
            sig.append({"name": s.name, "role": s.role, "din": s.din, "membership_no": s.membership_no})
    return PriorSigned(entity=tb.entity, year_end=tb.period_ends.get(period, date(2026, 3, 31)), units_divisor=divisor,
                       figures=figures, signatories=sig, mapping=mapping)


def rollover(prior: PriorSigned, current: TrialBalance, *, mapping: MappingVersion | None = None,
             tolerance: Decimal = D("0.00")) -> RolloverResult:
    """Check the new TB's comparative column against the prior signed figures and carry forward."""
    diffs: list[ComparativeDifference] = []
    for key, signed in sorted(prior.figures.items()):
        # balance-sheet keys: PY closing balance; P&L keys: the prior year's activity
        tbv = _q2(presentation_sign(key) * current.by_key(key, "PY") / prior.units_divisor)
        if abs(tbv - signed) > tolerance:
            diffs.append(ComparativeDifference(key=key, signed=signed, tb=tbv, difference=tbv - signed))
    carried = mapping or prior.mapping
    diff = None
    if carried is not None:
        fresh = MappingVersion.from_tb(current, client=carried.client, suggest=False)
        merged = dict(carried.rows)
        for code, k in fresh.rows.items():
            merged.setdefault(code, k)
        new = MappingVersion(client=carried.client, name=carried.name, version=carried.version + 1, rows=merged)
        diff = new.diff(carried)
        carried = new
    next_ye = date(prior.year_end.year + 1, prior.year_end.month, prior.year_end.day)
    ok = not diffs
    msg = "comparatives agree with the signed prior-year figures" if ok else f"{len(diffs)} comparative figure(s) differ from the signed prior-year statements"
    return RolloverResult(ok=ok, year_end=next_ye, differences=diffs, mapping=carried, mapping_diff=diff,
                          signatories_to_reconfirm=[dict(s, reconfirm="yes") for s in prior.signatories], message=msg)


def signatories_from_prior(prior: PriorSigned) -> list[Signatory]:
    return [Signatory(s["name"] or "", s["role"] or "", din=s.get("din"), membership_no=s.get("membership_no")) for s in prior.signatories]
