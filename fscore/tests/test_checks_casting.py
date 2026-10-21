"""Casting family: CAST-01/02/03, PLUG-01/02, TYPED-01, HID-01/02, BAL-01/02."""

from __future__ import annotations

from pathlib import Path

from helpers_core import finding_ids, mini_statements

from fscore.config import EngagementConfig
from fscore.prepare.spec import SheetSpec, WorkbookSpec
from fscore.prepare.writer import write_workbook
from fscore.runner import run_checks

CASTING_RULES = {"CAST-01", "CAST-02", "CAST-03", "PLUG-01", "PLUG-02", "TYPED-01", "HID-01", "HID-02", "BAL-01", "BAL-02"}


def run(path: Path):
    return run_checks(path, EngagementConfig())


def test_clean_workbook_has_no_casting_findings(tmp_path: Path) -> None:
    res = run(mini_statements(tmp_path / "clean.xlsx"))
    assert [f for f in res.findings if f.rule_id in CASTING_RULES] == []
    assert res.run.selftest.match_rate == 1.0


def test_plain_sum_does_not_cast_on_printed_values(tmp_path: Path) -> None:
    def mutate(spec: WorkbookSpec) -> None:
        n = spec.sheet("Note 4")
        n.set(7, 3, formula="SUM(C4:C6)", style="amount_bold")
        n.set(7, 4, formula="SUM(D4:D6)", style="amount_bold")

    res = run(mini_statements(tmp_path / "cast.xlsx", mutate))
    cast = finding_ids(res, "CAST-01")
    assert ("Note 4", ["C7"]) in cast and ("Note 4", ["D7"]) in cast
    f = next(f for f in res.findings if f.rule_id == "CAST-01" and f.cells == ["C7"])
    assert str(f.difference) == "0.01" and f.printed == "80.67" and f.expected == "80.66"
    assert len(f.evidence) == 3


def test_plugs_reported_even_when_casting(tmp_path: Path) -> None:
    def mutate(spec: WorkbookSpec) -> None:
        n = spec.sheet("Note 4")
        n.set(7, 3, formula="SUMPRODUCT(ROUND(C4:C6,2))-0.01", style="amount_bold")
        n.set(9, 4, formula="D7-D8+0.25", style="amount_total")

    res = run(mini_statements(tmp_path / "plug.xlsx", mutate))
    assert ("Note 4", ["C7"]) in finding_ids(res, "PLUG-01")
    assert ("Note 4", ["D9"]) in finding_ids(res, "PLUG-02")
    plug = next(f for f in res.findings if f.rule_id == "PLUG-01")
    assert plug.severity.value == "High"


def test_hidden_row_inside_total(tmp_path: Path) -> None:
    def mutate(spec: WorkbookSpec) -> None:
        spec.sheet("Note 4").hidden_rows.add(5)

    res = run(mini_statements(tmp_path / "hid.xlsx", mutate))
    assert ("Note 4", ["C7"]) in finding_ids(res, "HID-01")


def test_invisible_and_error_content(tmp_path: Path) -> None:
    def mutate(spec: WorkbookSpec) -> None:
        n = spec.sheet("Note 4")
        n.set(8, 2, 1022000.0, style="white")  # rupee working figure in white font inside the print area
        n.set(2, 4, formula="#REF!+1", style="amount")
        n.hidden_rows.add(2)

    res = run(mini_statements(tmp_path / "inv.xlsx", mutate))
    hid = finding_ids(res, "HID-02")
    assert ("Note 4", ["B8"]) in hid
    assert ("Note 4", ["D2"]) in hid


def test_typed_total(tmp_path: Path) -> None:
    def mutate(spec: WorkbookSpec) -> None:
        n = spec.sheet("Note 4")
        n.set(4, 3, formula="50.114+0", style="amount")
        n.set(7, 3, 80.66, style="amount_bold")

    res = run(mini_statements(tmp_path / "typed.xlsx", mutate))
    assert ("Note 4", ["C7"]) in finding_ids(res, "TYPED-01")


def test_balance_sheet_not_tallying(tmp_path: Path) -> None:
    def mutate(spec: WorkbookSpec) -> None:
        spec.sheet("BS").set(14, 3, formula="C10-C13-C16+0.01", style="amount")

    res = run(mini_statements(tmp_path / "bal.xlsx", mutate))
    bal = [f for f in res.findings if f.rule_id == "BAL-01"]
    assert len(bal) == 1 and bal[0].period == "CY" and str(bal[0].difference) == "-0.01"


def _schedule(path: Path, *, bad_cross: bool = False, drop_total_cell: bool = False) -> Path:
    spec = WorkbookSpec()
    s = spec.add(SheetSpec("Note 3", role="note", selected=True))
    s.set(1, 1, "3. Property, plant and equipment", style="caption_bold")
    for c, h in zip(range(2, 7), ["Computers", "Furniture", "Office equipment", "Vehicles", "Total"], strict=True):
        s.set(3, c, h, style="header")
    data = [("As at April 1, 2025", [10.111, 20.222, 5.555, 1.0]), ("Additions", [1.114, 2.224, 0.334, 0.0]), ("Disposals", [-0.5, 0.0, 0.0, 0.0])]
    for i, (cap, vals) in enumerate(data):
        r = 5 + i
        s.set(r, 1, cap, style="caption")
        for j, v in enumerate(vals):
            s.set(r, 2 + j, v, style="amount")
        s.set(r, 6, formula=f"SUM(B{r}:E{r})" if (bad_cross and i == 1) else f"SUMPRODUCT(ROUND(B{r}:E{r},2))", style="amount")
    s.set(8, 1, "As at March 31, 2026", style="caption_bold")
    for j, c in enumerate("BCDE"):
        if drop_total_cell and c == "D":
            continue
        s.set(8, j + 2, formula=f"SUMPRODUCT(ROUND({c}5:{c}7,2))", style="amount_bold")
    s.set(8, 6, formula="SUM(F5:F7)", style="amount_bold")
    s.print_area = (1, 1, 8, 6)
    return write_workbook(spec, path)


def test_cross_casting_detects_downward_total(tmp_path: Path) -> None:
    res = run(_schedule(tmp_path / "x.xlsx", bad_cross=True))
    assert ("Note 3", ["F6"]) in finding_ids(res, "CAST-02")
    clean = run(_schedule(tmp_path / "y.xlsx"))
    assert finding_ids(clean, "CAST-02") == []


def test_missing_total_cell(tmp_path: Path) -> None:
    res = run(_schedule(tmp_path / "m.xlsx", drop_total_cell=True))
    assert ("Note 3", ["D8"]) in finding_ids(res, "CAST-03")
