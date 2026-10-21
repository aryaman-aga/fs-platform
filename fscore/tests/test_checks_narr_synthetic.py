"""Acceptance on the Module P synthetic generator: zero NARR/GC/RPT findings on clean workbooks,
and every injected narrative / Going Concern / related-party error detected at its expected cells."""

from __future__ import annotations

from pathlib import Path

import pytest

generator = pytest.importorskip("fscore.synthetic.generator")
inject = pytest.importorskip("fscore.synthetic.inject")

from fscore.config import EngagementConfig  # noqa: E402
from fscore.runner import run_checks  # noqa: E402

MINE = ("NARR", "GC", "RPT", "TXT-06")


def _cfg(layout: str, units: str) -> EngagementConfig:
    return EngagementConfig(units=units, period_type="spfs" if layout == "spfs" else "annual")


@pytest.mark.parametrize(("layout", "units", "exact"), [("annual", "lakhs", False), ("annual", "lakhs", True), ("spfs", "millions", False), ("spfs", "millions", True)])
def test_clean_synthetic_workbooks_have_no_findings(tmp_path: Path, layout: str, units: str, exact: bool) -> None:
    r = generator.make_workbook(tmp_path / "clean.xlsx", seed=7, layout=layout, units=units, exact=exact)
    res = run_checks(Path(r.path), _cfg(layout, units))
    mine = [(f.rule_id, f.sheet, f.cells, f.explanation) for f in res.findings if f.rule_id.startswith(MINE)]
    assert mine == []


def _my_injections() -> list[str]:
    return sorted(n for n, inj in inject.registry().items() if any(str(r).startswith(MINE) for r in inj.rules))


@pytest.mark.parametrize("name", _my_injections())
def test_injected_error_is_detected(tmp_path: Path, name: str) -> None:
    layout = "spfs" if name == "rpt_fy_equals_q4" else "annual"
    units = "millions" if layout == "spfs" else "lakhs"
    r = generator.make_workbook(tmp_path / f"{name}.xlsx", seed=7, layout=layout, units=units, inject=[name])
    res = run_checks(Path(r.path), _cfg(layout, units))
    found = [f for f in res.findings if f.rule_id.startswith(MINE)]
    for e in r.expected:
        if not e.rule_id.startswith(MINE) or not getattr(e, "required", True):
            continue
        hits = [f for f in found if f.rule_id == e.rule_id and f.sheet == e.sheet and (not e.cells or not f.cells or set(e.cells) & set(f.cells))]
        assert hits, f"{name}: expected {e.rule_id} at {e.sheet}!{e.cells}; got {[(f.rule_id, f.sheet, f.cells) for f in found]}"


def test_accumulated_losses_are_read_from_the_other_equity_note(tmp_path: Path) -> None:
    """02 §6.8: "accumulated losses" is the closing balance of the retained-earnings block in the other-equity
    note, so a Going Concern sentence quoting a stale figure is reported (and restated after a fix)."""
    import re

    from fscore.io.ooxml_patch import XlsxPatcher
    from fscore.narrative.metrics import compute_metrics
    from fscore.runner import build_context

    cfg = _cfg("annual", "lakhs")
    r = generator.make_workbook(tmp_path / "clean.xlsx", seed=7)
    ctx = build_context(Path(r.path), cfg)
    closing = compute_metrics(ctx).get("retained_earnings", "CY")
    assert closing is not None and closing.value < 0 and closing.ref is not None
    gc = next((sh.name, c.addr, c.value) for sh in ctx.print_sheets for c in sh.iter_cells()
              if isinstance(c.value, str) and "accumulated losses of" in c.value)
    stale = re.sub(r"(accumulated losses of ₹ )[\d,]+\.\d\d", r"\g<1>9,999.99", gc[2])
    assert stale != gc[2]
    p = XlsxPatcher(Path(r.path))
    p.set_text(gc[0], gc[1], stale)
    res = run_checks(p.write(tmp_path / "stale.xlsx"), cfg)
    hits = [f for f in res.findings if f.rule_id == "GC-01" and f.sheet == gc[0] and "retained_earnings" in f.explanation]
    assert hits and closing.printed in hits[0].explanation, [(f.rule_id, f.explanation) for f in res.findings]
