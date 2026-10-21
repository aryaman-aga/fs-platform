"""ENG-01 evaluator self-test / missing cached values; ENG-02 sheets changed since the last run."""

from __future__ import annotations

import zipfile
from pathlib import Path

from helpers_core import mini_statements

from fscore import api
from fscore.config import EngagementConfig
from fscore.io.ooxml_patch import XlsxPatcher

CFG = EngagementConfig()


def _strip_cached_values(src: Path, dst: Path) -> Path:
    """Simulate a workbook saved without calculation: drop <v> from formula cells."""
    import re

    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename.startswith("xl/worksheets/sheet"):
                data = re.sub(rb"(</f>|<f [^>]*/>)<v>[^<]*</v>", rb"\1", data)
            zout.writestr(info, data)
    return dst


def test_missing_cached_values_reported(tmp_path: Path) -> None:
    src = _strip_cached_values(mini_statements(tmp_path / "a.xlsx"), tmp_path / "nocalc.xlsx")
    res = api.check_workbook(src, CFG)
    eng = [f for f in res.findings if f.rule_id == "ENG-01"]
    assert eng and "without calculation" in eng[0].explanation
    assert res.run.selftest.missing_cache > 0


def test_inconsistent_cache_disables_fixes(tmp_path: Path) -> None:
    src = mini_statements(tmp_path / "b.xlsx")
    p = XlsxPatcher(src)
    # cached values that the formulas cannot produce (e.g. a file edited by a tool that does not recalculate)
    for addr in ("C10", "D10", "C15", "D15", "C17", "D17"):
        p.refresh_cached("BS", addr, 999.99)
    bad = p.write(tmp_path / "inconsistent.xlsx")
    res = api.check_workbook(bad, CFG)
    assert any(f.rule_id == "ENG-01" and f.severity.value == "High" for f in res.findings)
    plan = api.propose_fixes(bad, CFG, check_result=res)
    assert plan.edits == [] and any("self-test" in u for u in plan.unresolved)


def test_sheet_changed_since_last_run(tmp_path: Path) -> None:
    src = mini_statements(tmp_path / "c.xlsx")
    first = api.check_workbook(src, CFG)
    assert first.sheet_states and "Note 4" in first.sheet_states
    p = XlsxPatcher(src)
    p.hide_row("Note 4", 5)  # someone opened the file in Excel and changed visibility
    edited = p.write(tmp_path / "edited.xlsx")
    second = api.check_workbook(edited, CFG, previous_sheet_states=first.sheet_states)
    eng2 = [f for f in second.findings if f.rule_id == "ENG-02"]
    assert [f.sheet for f in eng2] == ["Note 4"]
    unchanged = api.check_workbook(src, CFG, previous_sheet_states=first.sheet_states)
    assert not [f for f in unchanged.findings if f.rule_id == "ENG-02"]
