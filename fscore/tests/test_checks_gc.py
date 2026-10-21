"""Going Concern rules GC-01…06 on a small synthetic entity (all figures invented)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from helpers_narr import GC_CLEAN, EntityOpts, build_entity, config, rules, run

from fscore.config import PeriodSpec
from fscore.models import Severity


def _gc(tmp_path: Path, name: str, paragraphs: list[str] | None = None, **kw: object):  # type: ignore[no-untyped-def]
    opts = EntityOpts(gc=paragraphs if paragraphs is not None else list(GC_CLEAN), **kw)  # type: ignore[arg-type]
    return run(build_entity(tmp_path / f"{name}.xlsx", opts))


def _with(idx: int, text: str) -> list[str]:
    gc = list(GC_CLEAN)
    gc[idx] = text
    return gc


def _finding(res, rule: str, cell: str | None = None):  # type: ignore[no-untyped-def]
    return [f for f in res.findings if f.rule_id == rule and (cell is None or cell in f.cells)]


def test_clean_note_has_no_findings_and_records_matches(tmp_path: Path) -> None:
    res = _gc(tmp_path, "clean")
    assert rules(res, "GC") == []
    gc_rows = [r for r in res.narrative if r["in_going_concern"]]
    assert gc_rows and all(r["status"] == "Matched" for r in gc_rows)


def test_gc01_wrong_figure_and_typed_text_proposal(tmp_path: Path) -> None:
    text = GC_CLEAN[0].replace("25.05", "25.04")
    res = _gc(tmp_path, "fig", _with(0, text))
    assert _finding(res, "GC-01", "A2")
    prop = _finding(res, "GC-06", "A2")
    assert prop and "25.05" in (prop[0].suggested_fix or "")
    assert not (prop[0].suggested_fix or "").startswith("=")  # typed text, never a formula


def test_gc01_stale_previous_year_figure(tmp_path: Path) -> None:
    text = GC_CLEAN[0].replace("517.95", "494.50")
    res = _gc(tmp_path, "stale", _with(0, text))
    f = _finding(res, "GC-01", "A2")
    assert f and "last year" in f[0].explanation


def test_gc01_one_decimal_rounding(tmp_path: Path) -> None:
    res = _gc(tmp_path, "round", _with(0, GC_CLEAN[0].replace("517.95", "517.9")))
    f = _finding(res, "GC-01", "A2")
    assert f and f[0].severity == Severity.MEDIUM
    assert "517.95" in (_finding(res, "GC-06", "A2")[0].suggested_fix or "")


def test_gc01_unit_suspicion(tmp_path: Path) -> None:
    res = _gc(tmp_path, "unit", _with(4, "(c) Cash and cash equivalents as at March 31, 2026 were ₹ 851.5 lakhs."))
    f = _finding(res, "GC-01", "A6")
    assert f and "factor" in f[0].explanation


def test_gc02_profit_wording_for_a_loss(tmp_path: Path) -> None:
    res = _gc(tmp_path, "sign", _with(0, GC_CLEAN[0].replace("has incurred a loss of", "has earned a profit of")))
    assert _finding(res, "GC-02", "A2")
    prop = _finding(res, "GC-06", "A2")[0].suggested_fix or ""
    assert "incurred a loss of ₹ 25.05" in prop


def test_gc02_claim_contradicted(tmp_path: Path) -> None:
    extra = "The current liabilities of the Company exceed its current assets as at March 31, 2026."
    res = _gc(tmp_path, "claim", [*GC_CLEAN[:6], extra, GC_CLEAN[6]])
    assert _finding(res, "GC-02")


def test_gc02_profit_and_loss_for_same_period(tmp_path: Path) -> None:
    extra = "The Company has earned profits during the year ended March 31, 2026 from its core operations."
    res = _gc(tmp_path, "both", [*GC_CLEAN[:6], extra, GC_CLEAN[6]])
    assert any("both" in f.explanation for f in _finding(res, "GC-02"))


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        (GC_CLEAN[0].replace("for the year ended March 31, 2026", "for the year ended June 30, 2026"), "June"),
        (GC_CLEAN[0].replace("as at March 31, 2026", "as at 31st June 2026"), "Malformed"),
        (GC_CLEAN[0].replace("for the year ended March 31, 2026", "for the year ended June 2026"), "June 2026"),
        (GC_CLEAN[0].replace("(previous year:", "(year ended March 31, 2024:"), "2024"),
    ],
)
def test_gc03_period_scope(tmp_path: Path, text: str, needle: str) -> None:
    res = _gc(tmp_path, "scope", _with(0, text))
    assert any(needle in f.explanation for f in _finding(res, "GC-03", "A2"))


def test_gc03_three_month_entity_must_not_say_year(tmp_path: Path) -> None:
    cfg = config(periods_in_scope=[PeriodSpec(key="Q4_CY", end=date(2026, 3, 31), months=3),
                                   PeriodSpec(key="Q4_PY", end=date(2025, 3, 31), months=3)])
    res = run(build_entity(tmp_path / "q.xlsx"), cfg)
    assert any("three-month" in f.explanation for f in res.findings if f.rule_id == "GC-03")


def test_gc04_hidden_factor_group_and_duplicates(tmp_path: Path) -> None:
    paras = list(GC_CLEAN)
    paras[0] = paras[0].replace("net worth of the Company", "net worth of the Group")
    paras.append(paras[5])  # duplicated paragraph
    res = _gc(tmp_path, "struct", paras, gc_hidden={3})
    texts = " | ".join(f.explanation for f in _finding(res, "GC-04"))
    assert "hidden" in texts and "Group" in texts and "identical" in texts
    assert "net worth of the Company" in (_finding(res, "GC-06", "A2")[0].suggested_fix or "")


def test_gc04_contradictory_uncertainty(tmp_path: Path) -> None:
    paras = [*GC_CLEAN, "A material uncertainty exists that may cast significant doubt.", "There is no material uncertainty."]
    res = _gc(tmp_path, "contra", paras)
    assert any("material uncertainty" in f.explanation for f in _finding(res, "GC-04"))


def test_gc05_tax_note_says_loss_in_a_profit_year(tmp_path: Path) -> None:
    res = _gc(tmp_path, "tax", tax_text="The Company is in loss and hence no provision for current tax has been made for the year.",
              pl_cy={"expenses": 1240.30})
    assert _finding(res, "GC-05")


def test_gc05_equity_infusion_differs_from_soce(tmp_path: Path) -> None:
    res = _gc(tmp_path, "eq", _with(2, "(a) The holding company has infused equity of ₹ 55.00 lakhs during the year ended March 31, 2026."))
    assert _finding(res, "GC-05", "A4")
    assert _finding(res, "GC-01", "A4")


def test_formula_driven_narrative_gets_typed_text_proposal(tmp_path: Path) -> None:
    from fscore.runner import build_context, run_checks

    formula = '"(c) Cash and cash equivalents as at March 31, 2026 were ₹ "&TEXT(BS!C12,"0.00")&" lakhs."'
    path = build_entity(tmp_path / "formula.xlsx", EntityOpts(gc_formulas={4: formula}))
    cfg = config()
    ctx = build_context(path, cfg)
    res = run_checks(path, cfg, context=ctx)
    prop = _finding(res, "GC-06", "A6")
    assert prop, "formula-driven narrative must be proposed as typed text"
    assert prop[0].suggested_fix == "(c) Cash and cash equivalents as at March 31, 2026 were ₹ 85.15 lakhs."
    edits = ctx.shared["text_proposals"]
    assert [(e.sheet, e.cell) for e in edits] == [("Note 29-34", "A6")]
    assert not edits[0].new_text.startswith("=")


def test_clean_note_has_no_proposals(tmp_path: Path) -> None:
    assert not _finding(_gc(tmp_path, "clean2"), "GC-06")
