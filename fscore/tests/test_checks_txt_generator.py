"""TXT / MULTI rules against the synthetic generator: clean workbooks stay clean, injected errors are found."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from helpers_checks_t import run_rules

from fscore.config import EngagementConfig

generator = pytest.importorskip("fscore.synthetic.generator")

MINE = ("TXT-01", "TXT-02", "TXT-03", "TXT-04", "TXT-05", "TXT-07", "MULTI-")
# The synthetic SPFS builder fills its hidden functional-currency columns from the
# post-injection INR values, so after `translation_gap` the FC Balance Sheet no longer
# balances either and MULTI-04's premise ("FC balances, INR does not") is not present.
KNOWN_FIXTURE_GAPS = {("spfs", "translation_gap", "MULTI-04")}


def _config(layout: str, res: object) -> EngagementConfig:
    units = res.books.entity.units  # type: ignore[attr-defined]
    return EngagementConfig(period_type="spfs" if layout == "spfs" else "annual", units=units)


@pytest.mark.parametrize("layout", ["annual", "spfs"])
def test_clean_synthetic_workbook_has_no_txt_or_multi_findings(tmp_path: Path, layout: str) -> None:
    res = generator.make_workbook(tmp_path / f"clean_{layout}.xlsx", seed=7, layout=layout)
    ctx = run_rules(res.path, MINE, _config(layout, res))
    assert [(f.rule_id, f.sheet, f.cells, f.explanation) for f in ctx.findings] == []


def _cases() -> list[tuple[str, str]]:
    out = []
    rules = generator.injection_rules()
    for layout in ("annual", "spfs"):
        for name in generator.list_injections(layout):
            if any(r.startswith(MINE) for r in rules[name]):
                out.append((layout, name))
    return out


@pytest.mark.parametrize(("layout", "name"), _cases())
def test_injected_errors_are_detected(tmp_path: Path, layout: str, name: str) -> None:
    res = generator.make_workbook(tmp_path / f"{layout}_{name}.xlsx", seed=7, layout=layout, inject=[name])
    ctx = run_rules(res.path, MINE, _config(layout, res))
    expected = [e for e in res.expected if e.rule_id.startswith(MINE) and e.required]
    assert expected
    for e in expected:
        hit = [f for f in ctx.findings if f.rule_id == e.rule_id and f.sheet == e.sheet and (set(e.cells) & set(f.cells) or not e.cells)]
        if (layout, name, e.rule_id) in KNOWN_FIXTURE_GAPS:
            if not hit:
                pytest.xfail("fixture: FC helper columns are filled after the injection (see module comment)")
            continue
        assert hit, f"{e.rule_id} not reported on {e.sheet} {e.cells}: {e.note}"
    # no unrelated TXT / MULTI findings appear because of one injected error
    unrelated = [f for f in ctx.findings if not any(f.rule_id == e.rule_id and f.sheet == e.sheet for e in res.expected)]
    assert unrelated == [], [(f.rule_id, f.sheet, f.cells, f.explanation) for f in unrelated]


# Findings a correct engine may legitimately add for injections aimed at other rules
# (the injection itself introduces the pattern). Reported to the generator owner as
# candidates for `required=False` collateral expectations.
KNOWN_COLLATERAL = {
    ("share_count_double", "MULTI-03"),  # the double-count pattern adds a stale "Balance as at June 30" row
    ("header_row_data", "MULTI-04"),  # SPFS: a 0.43-sized INR gap while the FC Balance Sheet balances
}


def _sweep_cases() -> list[tuple[str, str]]:
    annual = list(generator.list_injections("annual"))
    spfs_only = [n for n in generator.list_injections("spfs") if n not in annual]
    return [("annual", n) for n in annual] + [("spfs", n) for n in spfs_only] + [("spfs", "header_row_data")]


@pytest.mark.parametrize(("layout", "name"), _sweep_cases())
def test_no_false_positives_from_other_injections(tmp_path: Path, layout: str, name: str) -> None:
    res = generator.make_workbook(tmp_path / f"fp_{layout}_{name}.xlsx", seed=7, layout=layout, inject=[name])
    ctx = run_rules(res.path, MINE, _config(layout, res))
    unexpected = [
        f for f in ctx.findings
        if not any(f.rule_id == e.rule_id and f.sheet == e.sheet for e in res.expected) and (name, f.rule_id) not in KNOWN_COLLATERAL
    ]
    assert unexpected == [], [(f.rule_id, f.sheet, f.cells, f.explanation) for f in unexpected]


@pytest.mark.parametrize("layout", ["annual", "spfs"])
def test_all_injections_at_once(tmp_path: Path, layout: str) -> None:
    names = list(generator.list_injections(layout))
    res = generator.make_workbook(tmp_path / f"all_{layout}.xlsx", seed=7, layout=layout, inject=names)
    ctx = run_rules(res.path, MINE, _config(layout, res))
    for e in res.expected:
        if not e.rule_id.startswith(MINE) or not e.required:
            continue
        source = e.note[1:].split("]", 1)[0] if e.note.startswith("[") else ""
        if (layout, source, e.rule_id) in KNOWN_FIXTURE_GAPS:
            continue
        hit = [f for f in ctx.findings if f.rule_id == e.rule_id and f.sheet == e.sheet and (set(e.cells) & set(f.cells) or not e.cells)]
        assert hit, f"{e.rule_id} not reported on {e.sheet} {e.cells} with every injection applied: {e.note}"
