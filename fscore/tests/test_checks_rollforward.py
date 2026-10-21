"""ROLL-01…05 on synthetic statements and small hand-built schedules."""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers_checks_x import config_for, found, run_rules, summary, write_small

from fscore.prepare.spec import SheetSpec, WorkbookSpec
from fscore.synthetic.generator import make_workbook


@pytest.mark.parametrize(("name", "rule"), [("roll_omits_movement", "ROLL-01"), ("py_formula_refs_cy", "ROLL-04"), ("dt_sign_convention", "ROLL-05")])
@pytest.mark.parametrize("layout", ["annual", "spfs"])
def test_injected_roll_errors_are_found(tmp_path: Path, name: str, rule: str, layout: str) -> None:
    res = make_workbook(tmp_path / f"{name}.xlsx", seed=7, layout=layout, inject=[name])
    findings = run_rules(res.path, config_for(layout))
    expected = [e for e in res.expected if e.rule_id == rule]
    assert expected
    for e in expected:
        assert found(findings, rule, e.sheet, *e.cells), (e, summary(findings))


def test_py_formula_reusing_cy_is_reported_once(tmp_path: Path) -> None:
    res = make_workbook(tmp_path / "py.xlsx", seed=7, inject=["py_formula_refs_cy"])
    e = next(e for e in res.expected if e.rule_id == "ROLL-04")
    findings = run_rules(res.path)
    at_cell = [f.rule_id for f in findings if f.sheet == e.sheet and e.cells[0] in f.cells]
    assert at_cell == ["ROLL-04"]  # the broken roll at the same cell is not reported a second time


def _sheet(spec: WorkbookSpec, title: str, headers: list[str]) -> SheetSpec:
    s = spec.add(SheetSpec("Notes", role="note", selected=True))
    s.set(1, 1, "Synthetic Widgets Private Limited", style="title")
    s.set(2, 1, "Notes to the financial statements for the year ended March 31, 2026", style="subtitle")
    s.set(6, 1, title, style="section")
    for i, h in enumerate(headers):
        s.set(7, 1 + i, h, style="header" if i else "header_left")
    s.col_widths = {1: 44, 2: 16, 3: 16, 4: 16, 5: 16}
    return s


def test_deferred_tax_table_with_inverted_oci_sign(tmp_path: Path) -> None:
    spec = WorkbookSpec()
    s = _sheet(spec, "8. Deferred tax assets (net)", ["Particulars", "As at April 1, 2025", "Recognised in profit and loss",
                                                       "Recognised in other comprehensive income", "As at March 31, 2026"])
    rows = [("Property, plant and equipment", 20.0, 5.0, 0.0), ("Employee benefits", 10.0, 2.0, -1.5)]
    for i, (cap, o, pl, oci) in enumerate(rows):
        r = 8 + i
        s.set(r, 1, cap, style="caption")
        s.set(r, 2, o, style="amount")
        s.set(r, 3, pl, style="amount")
        s.set(r, 4, oci, style="amount")
        s.set(r, 5, formula=f"B{r}+C{r}-D{r}", style="amount")  # adds the OCI credit with the opposite sign
    path = write_small(spec, tmp_path / "dt.xlsx")
    findings = run_rules(path)
    assert found(findings, "ROLL-05", "Notes", "E9"), summary(findings)
    assert not found(findings, "ROLL-05", "Notes", "E8")  # zero OCI: rolls either way
    assert not [f for f in findings if f.rule_id == "ROLL-01"]


def _ppe(net_cy_computers: float | None) -> WorkbookSpec:
    spec = WorkbookSpec()
    s = _sheet(spec, "3. Property, plant and equipment", ["Particulars", "Computers", "Furniture", "Total"])
    data = [
        (8, "Gross carrying amount", None), (9, "As at April 1, 2025", (100.0, 50.0)), (10, "Additions", (20.0, 10.0)),
        (11, "Disposals", (-5.0, 0.0)), (12, "As at March 31, 2026", "sum:9:11"),
        (13, "Accumulated depreciation", None), (14, "As at April 1, 2025", (40.0, 10.0)), (15, "Charge for the year", (15.0, 5.0)),
        (16, "Disposals", (-3.0, 0.0)), (17, "As at March 31, 2026", "sum:14:16"),
        (18, "Net carrying amount", None), (19, "As at April 1, 2025", "net:9:14"), (20, "As at March 31, 2026", "net:12:17"),
    ]
    for r, cap, vals in data:
        s.set(r, 1, cap, style="section" if vals is None else "caption")
        if vals is None:
            continue
        for c, letter in ((2, "B"), (3, "C")):
            if isinstance(vals, tuple):
                s.set(r, c, vals[c - 2], style="amount")
            elif vals.startswith("sum"):
                _k, a, b = vals.split(":")
                s.set(r, c, formula=f"SUM({letter}{a}:{letter}{b})", style="amount_bold")
            else:
                _k, a, b = vals.split(":")
                s.set(r, c, formula=f"{letter}{a}-{letter}{b}", style="amount_bold")
        s.set(r, 4, formula=f"SUM(B{r}:C{r})", style="amount_bold")
    if net_cy_computers is not None:
        s.set(20, 2, net_cy_computers, style="amount_bold")
    return spec


def test_ppe_schedule_with_disposals_rolls_cleanly(tmp_path: Path) -> None:
    assert run_rules(write_small(_ppe(None), tmp_path / "ppe_ok.xlsx")) == []


def test_net_block_typed_wrong(tmp_path: Path) -> None:
    findings = run_rules(write_small(_ppe(60.0), tmp_path / "ppe_bad.xlsx"))
    assert found(findings, "ROLL-03", "Notes", "B20"), summary(findings)
    f = next(f for f in findings if f.rule_id == "ROLL-03")
    assert f.expected == "63.00"


def _lease(cy_opening: float, *, hidden_transfer: bool = False) -> WorkbookSpec:
    spec = WorkbookSpec()
    s = _sheet(spec, "15. Lease liabilities", ["Particulars", "Year ended March 31, 2026", "Year ended March 31, 2025"])
    s.set(8, 1, "Balance at the beginning of the year", style="caption")
    s.set(8, 2, cy_opening, style="amount")
    s.set(8, 3, 100.0, style="amount")
    s.set(9, 1, "Additions", style="caption")
    s.set(9, 2, 30.0, style="amount")
    s.set(9, 3, 25.0, style="amount")
    s.set(10, 1, "Less: Payments", style="caption")
    s.set(10, 2, 10.0, style="amount")
    s.set(10, 3, 5.0, style="amount")
    s.set(11, 1, "Transfer from provisions", style="caption")
    s.set(11, 2, 4.0 if hidden_transfer else 0.0, style="amount")
    s.set(11, 3, 0.0, style="amount")
    if hidden_transfer:
        s.hidden_rows.add(11)
    s.set(12, 1, "Balance at the end of the year", style="caption_bold")
    s.set(12, 2, formula="B8+B9-B10+B11", style="amount_bold")
    s.set(12, 3, formula="C8+C9-C10+C11", style="amount_bold")
    return spec


def test_labelled_roll_with_deduction_rows_is_clean(tmp_path: Path) -> None:
    assert run_rules(write_small(_lease(120.0), tmp_path / "lease_ok.xlsx")) == []


def test_cy_opening_differs_from_py_closing(tmp_path: Path) -> None:
    findings = run_rules(write_small(_lease(121.0), tmp_path / "lease_open.xlsx"))
    assert found(findings, "ROLL-02", "Notes", "B8"), summary(findings)


def test_hidden_movement_breaks_the_printed_roll(tmp_path: Path) -> None:
    findings = run_rules(write_small(_lease(120.0, hidden_transfer=True), tmp_path / "lease_hidden.xlsx"))
    roll = [f for f in findings if f.rule_id == "ROLL-01" and f.cells == ["B12"]]
    assert roll and "Hidden row" in roll[0].explanation
