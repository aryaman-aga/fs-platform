"""Acceptance on the synthetic fixture set (02 §13.7, build pack 10 §4).

* every injected High error is detected at the expected sheet/cells (100 %);
* findings not explained by an injected, collateral or natural expectation stay
  at or below 5 % of all findings;
* the clean workbooks (annual and SPFS) produce no findings at all;
* after `fix`, every printed total casts, both balance sheets tally and both cash
  flows tie (rounding gaps solved through the designated lines), with zero
  unintended cell changes (the post-write verification diff).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from fscore import api
from fscore.config import EngagementConfig
from fscore.models import CheckResult, Finding
from fscore.synthetic.generator import list_injections, make_workbook
from fscore.synthetic.inject import ExpectedFinding

REPORT = Path(__file__).resolve().parents[2] / "var" / "acceptance.json"
ANNUAL = sorted(list_injections("annual"))
SPFS_ONLY = sorted(set(list_injections("spfs")) - set(ANNUAL))


def config_for(layout: str) -> EngagementConfig:
    if layout == "spfs":
        return EngagementConfig(period_type="spfs", units="millions")
    return EngagementConfig()


def _matches(f: Finding, e: ExpectedFinding) -> bool:
    if f.rule_id != e.rule_id:
        return False
    if e.sheet and (f.sheet or "").strip().lower() != e.sheet.strip().lower():
        return False
    return not e.cells or bool(set(e.cells) & set(f.cells))


def changed_cells(clean: Path, injected: Path) -> set[tuple[str, str]]:
    """(sheet, cell) whose formula, value, format or visibility differs from the clean workbook.

    A finding that touches such a cell is a genuine consequence of the injected error
    (collateral), not a false positive.
    """
    from fscore.diff import diff

    d = diff(clean, injected, EngagementConfig())
    out = {(c.sheet.lower(), c.cell) for c in d.changes}
    out |= {(m.sheet.lower(), m.cell) for m in d.printed_moves}
    for h in d.hidden_changes:  # e.g. "Note 3-5: row 12 hidden"
        sheet, _, what = h.partition(": ")
        m = re.match(r"row (\d+)", what)
        if m:
            out |= {(sheet.lower(), f"{col}{m.group(1)}") for col in "ABCDEFGHIJKLMNOP"}
    return out


def _touches(f: Finding, cells: set[tuple[str, str]]) -> bool:
    refs = {((f.sheet or "").lower(), c.split("!")[-1]) for c in f.cells}
    refs |= {(e.ref.sheet.lower(), e.ref.addr) for e in f.evidence if e.ref is not None}
    return bool(refs & cells)


def _score(result: CheckResult, expected: list[ExpectedFinding], changed: set[tuple[str, str]] | None = None) -> dict[str, Any]:
    required_high = [e for e in expected if e.required and e.severity == "High"]
    missed = [e for e in required_high if not any(_matches(f, e) for f in result.findings)]
    real = [f for f in result.findings if f.severity.value != "Info"]  # informational proposals are not assertions
    unexplained = [f for f in real if not any(_matches(f, e) for e in expected) and not (changed and _touches(f, changed))]
    return {"required_high": len(required_high), "missed": missed, "findings": len(real), "unexplained": unexplained}


@pytest.mark.parametrize("layout", ["annual", "spfs"])
def test_clean_workbooks_have_no_findings(layout: str, tmp_path: Path) -> None:
    res = make_workbook(tmp_path / f"clean-{layout}.xlsx", seed=7, layout=layout, units="millions" if layout == "spfs" else "lakhs")
    result = api.check_workbook(res.path, config_for(layout))
    assert result.run.selftest.match_rate == 1.0
    noise = [(f.rule_id, f.sheet, f.cells, f.explanation[:90]) for f in result.findings if f.severity.value != "Info"]
    assert noise == []


@pytest.mark.parametrize("name", ANNUAL + SPFS_ONLY)
def test_injected_error_is_detected(name: str, tmp_path: Path) -> None:
    layout = "annual" if name in ANNUAL else "spfs"
    res = make_workbook(tmp_path / f"{name}.xlsx", seed=7, layout=layout, inject=[name], units="millions" if layout == "spfs" else "lakhs")
    result = api.check_workbook(res.path, config_for(layout))
    score = _score(result, res.expected)
    assert not score["missed"], [f"{e.rule_id} {e.sheet}!{','.join(e.cells)} {e.note}" for e in score["missed"]]


def test_detection_and_false_positive_rates(tmp_path: Path) -> None:
    """Aggregate metrics over every injection, individually and all combined; writes var/acceptance.json."""
    totals: Counter[str] = Counter()
    per: dict[str, Any] = {}
    unexplained_rules: Counter[str] = Counter()
    clean = {lay: make_workbook(tmp_path / f"clean-{lay}.xlsx", seed=7, layout=lay, units="millions" if lay == "spfs" else "lakhs").path
             for lay in ("annual", "spfs")}
    for name in ANNUAL + SPFS_ONLY:
        layout = "annual" if name in ANNUAL else "spfs"
        res = make_workbook(tmp_path / f"{name}.xlsx", seed=7, layout=layout, inject=[name], units="millions" if layout == "spfs" else "lakhs")
        s = _score(api.check_workbook(res.path, config_for(layout)), res.expected, changed_cells(clean[layout], res.path))
        totals["required_high"] += s["required_high"]
        totals["missed"] += len(s["missed"])
        totals["findings"] += s["findings"]
        totals["unexplained"] += len(s["unexplained"])
        unexplained_rules.update(f.rule_id for f in s["unexplained"])
        per[name] = {"required_high": s["required_high"], "missed": len(s["missed"]), "findings": s["findings"], "unexplained": len(s["unexplained"]),
                     "unexplained_detail": [f"{f.rule_id} {f.sheet}!{','.join(f.cells[:3])}: {f.explanation[:100]}" for f in s["unexplained"]]}
    detection = 1.0 if not totals["required_high"] else 1 - totals["missed"] / totals["required_high"]
    fp_rate = 0.0 if not totals["findings"] else totals["unexplained"] / totals["findings"]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({"detection_rate": detection, "false_positive_rate": fp_rate, "totals": dict(totals),
                                  "unexplained_by_rule": dict(unexplained_rules), "per_injection": per}, indent=2), encoding="utf-8")
    assert detection == 1.0
    assert fp_rate <= 0.05, dict(unexplained_rules)


@pytest.mark.parametrize("layout", ["annual", "spfs"])
def test_fix_closes_casting_and_rounding_gaps(layout: str, tmp_path: Path) -> None:
    """Exact-mode books carry natural print-rounding differences; fix must leave everything casting and tied."""
    res = make_workbook(tmp_path / f"exact-{layout}.xlsx", seed=11, exact=True, layout=layout, units="millions" if layout == "spfs" else "lakhs")
    cfg = config_for(layout)
    before = api.check_workbook(res.path, cfg)
    assert any(f.rule_id == "CAST-01" for f in before.findings), "exact-mode books should not cast before the fix"
    plan = api.propose_fixes(res.path, cfg, check_result=before)
    out = api.apply_fixes(res.path, plan, tmp_path / f"exact-{layout} - casting corrected.xlsx", config=cfg)
    assert out.ok, (out.message, [(s.name, s.detail) for s in out.verification.steps])
    after = api.check_workbook(out.out_path or "", cfg)
    assert [f for f in after.findings if f.rule_id in ("CAST-01", "CAST-02", "CAST-03", "PLUG-01")] == []
    too_large = {(b.statement, b.period) for b in plan.balancing if b.status != "applied"}
    gaps = res.gaps or {}
    small = all(abs(Decimal(str(v))) <= cfg.policy.balancing_threshold for g in gaps.values() for v in g.values())
    if small:
        assert not too_large, plan.balancing
        assert [f for f in after.findings if f.rule_id in ("BAL-01", "BAL-02")] == []
    # idempotent: a second plan on the corrected copy changes nothing
    again = api.propose_fixes(out.out_path or "", cfg)
    assert [e for e in again.edits if e.fix_kind.value == "auto"] == []


def test_all_injections_combined(tmp_path: Path) -> None:
    """Every annual injection at once. Injections that hit the same cells can mask each other (e.g. a paragraph
    moved into hidden rows by one injector holds another injector's malformed date; a duplicated party row
    re-inserts a balance another injector omitted), so ≥ 95 % is required here; 100 % holds individually."""
    res = make_workbook(tmp_path / "everything.xlsx", seed=7, inject=ANNUAL)
    result = api.check_workbook(res.path, config_for("annual"))
    score = _score(result, res.expected)
    detected = 1 - len(score["missed"]) / max(score["required_high"], 1)
    assert detected >= 0.95, [f"{e.rule_id} {e.sheet}!{','.join(e.cells)} {e.note}" for e in score["missed"]]
    assert not result.run.warnings, result.run.warnings  # no check crashed on the heavily damaged workbook


@pytest.mark.parametrize("layout", ["annual", "spfs"])
def test_fix_plus_approved_narrative_leaves_no_findings(layout: str, tmp_path: Path) -> None:
    """The full loop: auto fixes + the typed-text narrative updates the plan proposes (approved) → nothing left."""
    res = make_workbook(tmp_path / f"loop-{layout}.xlsx", seed=11, exact=True, layout=layout, units="millions" if layout == "spfs" else "lakhs")
    cfg = config_for(layout)
    plan = api.propose_fixes(res.path, cfg, check_result=api.check_workbook(res.path, cfg))
    narrative = {e.id for e in plan.edits if e.kind.value == "text"}
    assert narrative, "figures moved by the fix must come with restated narrative proposals"
    out = api.apply_fixes(res.path, plan, tmp_path / "loop - corrected.xlsx", config=cfg, approved_edit_ids=narrative)
    assert out.ok, out.message
    after = api.check_workbook(out.out_path or "", cfg)
    assert [(f.rule_id, f.sheet, f.cells) for f in after.findings if f.severity.value != "Info"] == []
