"""Module P entry point: TB + mapping + supplementary schedules -> statements workbook.

``prepare`` runs the TB validation gate (E-DATA-01) first and writes nothing
when it blocks. Totals are written in the cast-safe house form so every
printed total adds its printed components; any remaining Balance Sheet /
cash-flow rounding gap is left to the fix engine's balancing lines, and the
whole workbook then goes through Module R before release.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from fscore.io.ooxml import sha256_file
from fscore.prepare.disclosure import ChecklistReport, run_checklist
from fscore.prepare.inputs import UNIT_DIVISORS, UNIT_LABELS, EntityInfo, PrepareInputs, YearData
from fscore.prepare.mapping import MappingDiff, MappingVersion
from fscore.prepare.statements import build_spec, finalise
from fscore.prepare.tb import TBIssue, TBValidationResult, TrialBalance, validate_tb
from fscore.prepare.writer import write_workbook

REQUIRED_SUPPLEMENTARY = {
    "far": "fixed-asset register movements by class",
    "rou": "right-of-use asset movements",
    "software": "intangible asset movements",
    "cwip": "capital work-in-progress movements",
    "lease": "lease-liability movements",
    "gratuity": "actuarial valuation (DBO movement)",
    "term_loan": "term-loan movements",
    "holding_loan": "group-loan movements",
    "shares": "share register (opening / issued / closing, face value)",
    "ctl": "income-tax payments",
    "payable_ageing": "payables ageing",
}


class PrepareResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    ok: bool
    out_path: str | None = None
    sha256: str | None = None
    tb_validation: TBValidationResult
    unmapped: list[str] = Field(default_factory=list)
    mapping_diff: MappingDiff | None = None
    missing_supplementary: list[str] = Field(default_factory=list)
    templates: dict[str, str] = Field(default_factory=dict)
    disclosure: ChecklistReport | None = None
    index: dict[str, Any] = Field(default_factory=dict)
    message: str = ""


def check_supplementary(years: dict[str, YearData]) -> list[str]:
    missing = []
    for yk in ("CY", "PY"):
        yd = years.get(yk)
        if yd is None:
            missing.append(f"{yk}: all supplementary schedules")
            continue
        for attr, label in REQUIRED_SUPPLEMENTARY.items():
            v = getattr(yd, attr)
            if (isinstance(v, dict) and not v) or (attr in ("rou", "software", "cwip") and v.closing == 0 and v.opening == 0 and v.additions == 0):
                if attr in ("rou", "software", "cwip"):
                    continue  # a nil schedule is legitimate
                missing.append(f"{yk}: {label}")
    return missing


def prepare(
    tb: TrialBalance,
    mapping: MappingVersion | None,
    supplementary: dict[str, YearData],
    entity: EntityInfo,
    out_path: str | Path,
    *,
    opening: dict[str, Decimal] | None = None,
    layout: str = "annual",
    units: str = "lakhs",
    previous_mapping: MappingVersion | None = None,
    cast_safe: bool = True,
    checklist_answers: dict[str, Any] | None = None,
    quarter_fraction: dict[str, Decimal] | None = None,
    fx: dict[str, dict[str, Decimal]] | None = None,
) -> PrepareResult:
    unmapped: list[str] = []
    diff = None
    if mapping is not None:
        unmapped = tb.apply_mapping(mapping)
        diff = mapping.diff(previous_mapping) if previous_mapping is not None else None
    expected = {"CY": entity.year_end, "PY": entity.py_end}
    gate = validate_tb(tb, expected_period_ends=expected, presentation_currency=entity.presentation_currency)
    if unmapped and not any(i.code == "TB-UNMAPPED" for i in gate.issues):
        gate.issues.append(TBIssue(code="TB-UNMAPPED", severity="block", message=f"{len(unmapped)} account(s) are not in the mapping.", accounts=unmapped[:200]))
        gate.ok = False
    missing = check_supplementary(supplementary)
    if not gate.ok or missing:
        why = "; ".join(i.message for i in gate.blocking) or ""
        if missing:
            why = (why + "; " if why else "") + "missing supplementary schedules: " + ", ".join(missing)
        return PrepareResult(ok=False, tb_validation=gate, unmapped=unmapped, mapping_diff=diff, missing_supplementary=missing,
                             message=f"Preparation blocked by the TB validation gate: {why}")
    if units not in UNIT_DIVISORS:
        raise ValueError(f"unknown units {units!r}")
    entity.units = units
    entity.unit_label = UNIT_LABELS[units]
    inp = PrepareInputs(entity=entity, tb=tb, years=supplementary, opening=dict(opening or {}), units=units, layout=layout,
                        exact=True, cast_safe_totals=cast_safe, quarter_fraction=dict(quarter_fraction or {}), fx=dict(fx or {}))
    cx = build_spec(inp)
    values = finalise(cx)
    out = write_workbook(cx.spec, out_path, values, shared_formulas=True)
    report = run_checklist(out, checklist_answers)
    return PrepareResult(ok=True, out_path=str(out), sha256=sha256_file(out), tb_validation=gate, unmapped=unmapped, mapping_diff=diff,
                         templates=dict(cx.spec.templates), disclosure=report, index=dict(cx.spec.entity.get("index", {})),
                         message=f"Prepared {Path(out).name}: {report.completion}% of the disclosure checklist answered, {len(report.blocking)} blocking item(s).")


def prepare_from_books(books: Any, out_path: str | Path, **kw: Any) -> PrepareResult:
    """Convenience: run ``prepare`` on synthetic books (tests, demos)."""
    mapping = MappingVersion.from_tb(books.tb, client=books.entity.name, suggest=False)
    return prepare(books.tb, mapping, books.years, books.entity, out_path, opening=books.opening, units=books.units,
                   quarter_fraction=books.quarter_fraction, fx=books.fx, **kw)


def today() -> date:
    return date.today()
