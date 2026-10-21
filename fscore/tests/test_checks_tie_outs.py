"""XREF-03 mandatory tie-outs on synthetic statements."""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers_checks_x import cell_of, config_for, found, rewrite, run_rules, summary

from fscore.engine.refs import make_addr
from fscore.prepare.spec import WorkbookSpec
from fscore.synthetic.generator import make_workbook


@pytest.mark.parametrize("name", ["plug_hides_difference", "cf_components_inconsistent", "same_figure_two_values", "pat_points_at_tci",
                                  "eps_cy_equals_py"])
@pytest.mark.parametrize("layout", ["annual", "spfs"])
def test_injected_tie_out_errors_are_found(tmp_path: Path, name: str, layout: str) -> None:
    res = make_workbook(tmp_path / f"{name}.xlsx", seed=7, layout=layout, inject=[name])
    findings = run_rules(res.path, config_for(layout))
    for e in res.expected:
        if e.rule_id == "XREF-03" and e.required:
            assert found(findings, e.rule_id, e.sheet, *e.cells), (e, summary(findings))


def test_soce_profit_row_reports_component_then_total(tmp_path: Path) -> None:
    res = make_workbook(tmp_path / "soce.xlsx", seed=7, inject=["same_figure_two_values"])
    f = next(f for f in run_rules(res.path) if f.rule_id == "XREF-03" and f.sheet == "SOCIE" and "D8" in f.cells)
    assert f.cells == ["D8", "E8"]


def test_bs_line_that_disagrees_with_its_note(tmp_path: Path) -> None:
    res = make_workbook(tmp_path / "base.xlsx", seed=7)
    sheet, row, col = cell_of(res, "bs.trade_receivables", "CY") if "bs.trade_receivables" in res.index else cell_of(res, "bs.tr", "CY")

    def mutate(spec: WorkbookSpec) -> None:
        cs = spec.sheet(sheet).cells[(row, col)]
        cs.formula = None
        cs.value = 999.99

    path = rewrite(res, mutate, tmp_path / "bs_vs_note.xlsx")
    findings = run_rules(path)
    assert found(findings, "XREF-03", sheet, make_addr(row, col)), summary(findings)


def test_working_capital_movement_not_from_balance_sheet(tmp_path: Path) -> None:
    res = make_workbook(tmp_path / "base.xlsx", seed=7)
    target = next(k for k in res.index if k.startswith("cf.wc_tp") or k == "cf.wc_tp")

    sheet, row, col = cell_of(res, target, "CY")

    def mutate(spec: WorkbookSpec) -> None:
        cs = spec.sheet(sheet).cells[(row, col)]
        cs.formula = None
        cs.value = 12.34

    path = rewrite(res, mutate, tmp_path / "wc.xlsx")
    findings = run_rules(path)
    assert found(findings, "XREF-03", sheet, make_addr(row, col)), summary(findings)


def test_no_tie_out_findings_after_the_fix_engine(tmp_path: Path) -> None:
    """Totals-only rewrite + balancing lines (+adj) must not create cross-reference noise."""
    from fscore import api

    cfg = config_for("annual")
    res = make_workbook(tmp_path / "exact.xlsx", seed=11, exact=True)
    before = api.check_workbook(res.path, cfg)
    plan = api.propose_fixes(res.path, cfg, check_result=before)
    out = tmp_path / "exact - casting corrected.xlsx"
    applied = api.apply_fixes(res.path, plan, out, config=cfg, approved_edit_ids={e.id for e in plan.edits})
    assert applied.ok, applied.message
    assert any(b.status == "applied" for b in plan.balancing)
    assert run_rules(out, cfg) == []
