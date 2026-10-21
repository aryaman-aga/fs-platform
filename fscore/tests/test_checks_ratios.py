"""RATIO-01…03: recomputation, definition / caption consistency, unexplained variances."""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers_checks_x import cell_of, config_for, found, rewrite, run_rules, summary

from fscore.engine.refs import make_addr
from fscore.prepare.spec import WorkbookSpec
from fscore.synthetic.generator import make_workbook


@pytest.mark.parametrize(("name", "rule"), [("ratio_avg_twice", "RATIO-01"), ("ratio_roce_pbt", "RATIO-02"), ("ratio_no_reason", "RATIO-03")])
@pytest.mark.parametrize("layout", ["annual", "spfs"])
def test_injected_ratio_errors_are_found(tmp_path: Path, name: str, rule: str, layout: str) -> None:
    res = make_workbook(tmp_path / f"{name}.xlsx", seed=7, layout=layout, inject=[name])
    findings = run_rules(res.path, config_for(layout))
    expected = [e for e in res.expected if e.rule_id == rule]
    assert expected
    for e in expected:
        assert found(findings, rule, e.sheet, *e.cells), (e, summary(findings))


def test_average_divided_twice_is_explained(tmp_path: Path) -> None:
    res = make_workbook(tmp_path / "avg.xlsx", seed=7, inject=["ratio_avg_twice"])
    f = next(f for f in run_rules(res.path) if f.rule_id == "RATIO-01")
    assert "divided by 2 twice" in f.explanation


def test_roce_on_pbt_is_one_definition_finding(tmp_path: Path) -> None:
    res = make_workbook(tmp_path / "roce.xlsx", seed=7, inject=["ratio_roce_pbt"])
    ratio = [f for f in run_rules(res.path) if f.rule_id.startswith("RATIO")]
    assert [f.rule_id for f in ratio] == ["RATIO-02"]


def test_variance_cells_reported_for_unexplained_change(tmp_path: Path) -> None:
    res = make_workbook(tmp_path / "reason.xlsx", seed=7, inject=["ratio_no_reason"])
    f = next(f for f in run_rules(res.path) if f.rule_id == "RATIO-03")
    assert len(f.cells) == 2 and "25 %" in f.explanation


def test_current_ratio_typed_wrong(tmp_path: Path) -> None:
    res = make_workbook(tmp_path / "base.xlsx", seed=7)
    sheet, row, col = cell_of(res, "note.ratio.current_ratio", "CY")

    def mutate(spec: WorkbookSpec) -> None:
        cs = spec.sheet(sheet).cells[(row, col)]
        cs.formula = None
        cs.value = 9.99

    findings = run_rules(rewrite(res, mutate, tmp_path / "cr.xlsx"))
    assert found(findings, "RATIO-01", sheet, make_addr(row, col)), summary(findings)
