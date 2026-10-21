"""SUB-01…05 and SUB-08: ageing, MSME, employee benefits, leases, revenue basis, financial instruments."""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers_checks_x import cell_of, config_for, found, rewrite, run_rules, summary

from fscore.engine.refs import make_addr
from fscore.prepare.spec import WorkbookSpec
from fscore.synthetic.generator import make_workbook


@pytest.mark.parametrize(("name", "rule"), [
    ("ageing_vs_note", "SUB-01"), ("ageing_total_missing", "SUB-01"), ("actuarial_split", "SUB-03"), ("expense_vs_actuarial", "SUB-03"),
    ("ind_as_115_basis", "SUB-05"), ("fi_table_incomplete", "SUB-08"),
])
@pytest.mark.parametrize("layout", ["annual", "spfs"])
def test_injected_schedule_errors_are_found(tmp_path: Path, name: str, rule: str, layout: str) -> None:
    res = make_workbook(tmp_path / f"{name}.xlsx", seed=7, layout=layout, inject=[name])
    findings = run_rules(res.path, config_for(layout))
    expected = [e for e in res.expected if e.rule_id == rule]
    assert expected
    for e in expected:
        assert found(findings, rule, e.sheet, *e.cells), (e, summary(findings))


def _find_row(spec: WorkbookSpec, sheet: str, text: str) -> int:
    return next(r for (r, c), cs in spec.sheet(sheet).cells.items() if c == 1 and isinstance(cs.value, str) and text in cs.value)


def test_msme_disclosure_differs_from_trade_payables_note(tmp_path: Path) -> None:
    res = make_workbook(tmp_path / "base.xlsx", seed=7)
    row = _find_row(res.spec, "Note 14-19", "Principal amount remaining unpaid")

    def mutate(spec: WorkbookSpec) -> None:
        cs = spec.sheet("Note 14-19").cells[(row, 2)]
        cs.formula = None
        cs.value = 25.0

    findings = run_rules(rewrite(res, mutate, tmp_path / "msme.xlsx"))
    assert found(findings, "SUB-02", "Note 14-19", make_addr(row, 2)), summary(findings)


def test_lease_interest_differs_from_finance_costs(tmp_path: Path) -> None:
    res = make_workbook(tmp_path / "base.xlsx", seed=7)
    row = _find_row(res.spec, "Note 14-19", "Finance cost accrued during the year")

    def mutate(spec: WorkbookSpec) -> None:
        cs = spec.sheet("Note 14-19").cells[(row, 2)]
        cs.formula = None
        cs.value = 3.21

    findings = run_rules(rewrite(res, mutate, tmp_path / "lease.xlsx"))
    assert found(findings, "SUB-04", "Note 14-19", make_addr(row, 2)), summary(findings)


def test_investments_at_cost_are_not_required_in_the_fi_note(tmp_path: Path) -> None:
    res = make_workbook(tmp_path / "base.xlsx", seed=7)
    sheet, row, col = cell_of(res, "bs.investments", "CY")
    findings = run_rules(res.path)
    assert not found(findings, "SUB-08", sheet, make_addr(row, col))
