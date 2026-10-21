"""SUB-06 share capital arithmetic and SUB-07 shareholding percentages."""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers_checks_x import config_for, found, rewrite, run_rules, summary

from fscore.prepare.spec import WorkbookSpec
from fscore.synthetic.generator import make_workbook


@pytest.mark.parametrize("layout", ["annual", "spfs"])
def test_share_count_double_counted_subtotal(tmp_path: Path, layout: str) -> None:
    res = make_workbook(tmp_path / "dbl.xlsx", seed=7, layout=layout, inject=["share_count_double"])
    findings = run_rules(res.path, config_for(layout))
    e = next(e for e in res.expected if e.rule_id == "SUB-06")
    assert found(findings, "SUB-06", e.sheet, e.cells[0]), summary(findings)  # the count's SUM double-counts the interim row
    assert found(findings, "SUB-06", e.sheet, e.cells[1]), summary(findings)  # closing amount ≠ issued capital
    dbl = next(f for f in findings if f.rule_id == "SUB-06" and f.cells == [e.cells[0]])
    assert "double-counts" in dbl.explanation


@pytest.mark.parametrize("layout", ["annual", "spfs"])
def test_promoter_percentage_on_another_basis(tmp_path: Path, layout: str) -> None:
    res = make_workbook(tmp_path / "pct.xlsx", seed=7, layout=layout, inject=["shareholding_pct_basis"])
    findings = run_rules(res.path, config_for(layout))
    e = next(e for e in res.expected if e.rule_id == "SUB-07")
    assert found(findings, "SUB-07", e.sheet, *e.cells), summary(findings)


def test_caption_share_count_times_face_value(tmp_path: Path) -> None:
    res = make_workbook(tmp_path / "base.xlsx", seed=7)
    sh = res.spec.sheet("Note 12-13")
    row = next(r for (r, c), cs in sh.cells.items() if c == 1 and isinstance(cs.value, str) and "equity shares of" in cs.value
               and r > next(rr for (rr, cc), x in sh.cells.items() if cc == 1 and x.value == "Issued, subscribed and fully paid-up"))
    original = str(sh.cells[(row, 1)].value)

    def mutate(spec: WorkbookSpec) -> None:
        count = original.split(" ", 1)[0]
        new = f"{int(count.replace(',', '')) + 100_000:,}"
        spec.sheet("Note 12-13").cells[(row, 1)].value = original.replace(count, new, 1)

    findings = run_rules(rewrite(res, mutate, tmp_path / "caption.xlsx"))
    assert found(findings, "SUB-06", "Note 12-13", f"B{row}"), summary(findings)
    assert not found(findings, "SUB-06", "Note 12-13", f"C{row}")  # the comparative count is unchanged
