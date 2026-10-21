"""Engine controls: C3 balancing limits, C5 post-write verification, C11 period scope, E-ITGC-02 rule changes.

Gaps of an exact size are built by retyping one figure of a clean synthetic workbook and recomputing every
cached value (what saving the edit in Excel produces), so the copy stays internally consistent and the
defect under test is the only one.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import zipfile
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from importlib import resources
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from helpers_synthetic import cached_workbook
from lxml import etree

from fscore import api, registry
from fscore.config import EngagementConfig, FixPolicy
from fscore.engine.calc import Recalculator
from fscore.engine.evaluator import values_equal
from fscore.engine.refs import col_to_index, index_to_col, make_addr, split_addr
from fscore.engine.selftest import bounds, name_lookup
from fscore.fix.verify import changed_sheet_parts, diff_against_source, verify_copy
from fscore.io.ooxml import q
from fscore.io.ooxml_patch import XlsxPatcher
from fscore.io.xlsx_reader import Workbook
from fscore.models import (
    BalancingAdjustment,
    CellRef,
    CheckResult,
    Edit,
    EditGroup,
    EditKind,
    Finding,
    FixKind,
    FixPlan,
    Severity,
    VerificationReport,
)
from fscore.synthetic.generator import SyntheticResult

CFG = EngagementConfig()
SPFS = EngagementConfig(period_type="spfs", units="millions")
PAISA = Decimal("0.01")
Key = tuple[str, int, int]


# ----------------------------------------------------------------------------- helpers


def resave(src: Path, dest: Path, *, values: dict[tuple[str, str], float] | None = None,
           formulas: dict[tuple[str, str], str] | None = None) -> Path:
    """``src`` with typed values / formulas replaced and every cached value recomputed (an edit saved in Excel)."""
    typed = {(s, *split_addr(a)): v for (s, a), v in (values or {}).items()}
    linked = {(s, *split_addr(a)): f for (s, a), f in (formulas or {}).items()}
    with Workbook.open(src) as wb:
        cached = {(s.name, c.row, c.col): c.value for s in wb.sheets for c in s.iter_cells()}
        existing = {(s.name, c.row, c.col): c.formula for s in wb.sheets for c in s.iter_cells() if c.formula is not None}
        lower = {s.name.lower(): s.name for s in wb.sheets}

        def base(key: Key) -> Any:
            return typed[key] if key in typed else cached.get(key)

        rc = Recalculator({k: f for k, f in (existing | linked).items() if k not in typed}, base,
                          name_lookup=name_lookup(wb), bounds=bounds(wb), date1904=wb.date1904,
                          canon=lambda name: lower.get(name.strip().lower(), name))
        computed = rc.compute_all()
    patcher = XlsxPatcher(src)
    for (sheet, row, col), value in typed.items():
        patcher.set_value(sheet, make_addr(row, col), value)
    for key, value in computed.items():
        if key in linked:
            patcher.set_formula(key[0], make_addr(key[1], key[2]), linked[key], value)
        elif not values_equal(cached.get(key), value):
            patcher.refresh_cached(key[0], make_addr(key[1], key[2]), value)
    return patcher.write(dest)


def cell_of(res: SyntheticResult, alias: str, period: str) -> tuple[str, str]:
    sheet, row, cols = res.index[alias]
    return sheet, make_addr(row, int(cols[period]))


def cell_at(path: Path, sheet: str, addr: str) -> Any:
    with Workbook.open(path) as wb:
        return wb.sheet(sheet).cell(*split_addr(addr))


def typed_above(res: SyntheticResult, dest: Path, alias: str, period: str, delta: str) -> Path:
    """The workbook with ``alias`` typed ``delta`` above the figure it prints: a tie gap of exactly ``delta``."""
    sheet, addr = cell_of(res, alias, period)
    printed = Decimal(str(cell_at(res.path, sheet, addr).value)).quantize(PAISA)
    return resave(res.path, dest, values={(sheet, addr): float(printed + Decimal(delta))})


def only(result: CheckResult, rule_id: str) -> Finding:
    found = [f for f in result.findings if f.rule_id == rule_id]
    assert len(found) == 1, [(f.sheet, f.cells, f.difference) for f in found]
    return found[0]


def adjustment(plan: FixPlan, statement: str, period: str) -> BalancingAdjustment:
    (adj,) = [b for b in plan.balancing if b.statement == statement and b.period == period]
    return adj


def balancing_edits(plan: FixPlan) -> list[tuple[str, str]]:
    return [(e.sheet, e.cell) for e in plan.edits if e.group == EditGroup.BALANCING]


# ============================================================================= C3 balancing limited to rounding


@pytest.fixture(scope="module")
def annual() -> SyntheticResult:
    """Clean annual workbook: every total casts, both Balance Sheets tally, both cash flows tie."""
    return cached_workbook(seed=7)


@pytest.fixture(scope="module")
def bs_gap_021(annual: SyntheticResult, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Capital advances (CY) typed 0.21 above the TB: the Balance Sheet is out by one paisa more than the threshold."""
    return typed_above(annual, tmp_path_factory.mktemp("c3") / "bs-gap-0.21.xlsx", "n611.oa.nc.capadv", "CY", "0.21")


def test_c3_balance_sheet_gap_above_threshold_is_reported_not_balanced(annual: SyntheticResult, bs_gap_021: Path,
                                                                         tmp_path: Path) -> None:
    """C3: a Balance Sheet gap above the 0.20 threshold gets no balancing adjustment and stays a manual finding."""
    assert CFG.policy.balancing_threshold == Decimal("0.20")
    before = api.check_workbook(bs_gap_021, CFG)
    assert before.run.selftest.match_rate == 1.0  # the retyped copy is consistent: the gap is its only defect
    bal = only(before, "BAL-01")
    assert (bal.period, bal.difference, bal.fix_kind) == ("CY", Decimal("0.21"), FixKind.MANUAL)
    assert "exceeds the rounding threshold" in bal.explanation

    plan = api.propose_fixes(bs_gap_021, CFG, check_result=before)
    bs = adjustment(plan, "balance_sheet", "CY")
    assert (bs.status, bs.gap, bs.adjustment, bs.target) == ("too_large", Decimal("0.21"), Decimal(0), None)
    assert {b.status for b in plan.balancing} == {"too_large"}  # the cash-flow knock-on is not plugged either
    assert balancing_edits(plan) == []
    assert cell_of(annual, "note.other_expenses.misc", "CY") not in {(e.sheet, e.cell) for e in plan.edits}

    out = api.apply_fixes(bs_gap_021, plan, tmp_path / "corrected.xlsx", config=CFG)
    assert out.ok, (out.message, out.verification.steps)  # verified: the gap is reported, not hidden
    after = only(api.check_workbook(out.out_path or "", CFG), "BAL-01")
    assert (after.difference, after.fix_kind) == (Decimal("0.21"), FixKind.MANUAL)


def test_c3_gap_at_threshold_is_balanced_through_designated_line(annual: SyntheticResult, tmp_path: Path) -> None:
    """C3 boundary: a 0.20 gap is rounding, absorbed by one visible adjustment on 'Miscellaneous expenses'."""
    src = typed_above(annual, tmp_path / "bs-gap-0.20.xlsx", "n611.oa.nc.capadv", "CY", "0.20")
    before = api.check_workbook(src, CFG)
    assert only(before, "BAL-01").fix_kind == FixKind.AUTO
    plan = api.propose_fixes(src, CFG, check_result=before)
    misc = cell_of(annual, "note.other_expenses.misc", "CY")
    bs = adjustment(plan, "balance_sheet", "CY")
    assert (bs.status, bs.adjustment, bs.target) == ("applied", Decimal("-0.20"), CellRef(sheet=misc[0], addr=misc[1]))
    (edit,) = [e for e in plan.edits if e.group == EditGroup.BALANCING]
    assert ((edit.sheet, edit.cell), edit.fix_kind) == (misc, FixKind.AUTO)
    assert edit.new_formula == f"{edit.old_formula}-0.2"  # the adjustment stays visible in the formula
    out = api.apply_fixes(src, plan, tmp_path / "corrected.xlsx", config=CFG)
    assert out.ok, out.message
    assert [f.rule_id for f in api.check_workbook(out.out_path or "", CFG).findings if f.rule_id.startswith("BAL")] == []


def test_c3_threshold_is_the_engagement_fix_policy(bs_gap_021: Path) -> None:
    """C3: BAL-01 and the solver both read ``policy.balancing_threshold``: with 0.25 allowed, the 0.21 gap is balanced."""
    wider = EngagementConfig(policy=FixPolicy(balancing_threshold=Decimal("0.25")))
    check = api.check_workbook(bs_gap_021, wider)
    assert only(check, "BAL-01").fix_kind == FixKind.AUTO
    bs = adjustment(api.propose_fixes(bs_gap_021, wider, check_result=check), "balance_sheet", "CY")
    assert (bs.status, bs.adjustment) == ("applied", Decimal("-0.21"))


def test_c3_previous_adjustment_is_recomputed_not_topped_up(annual: SyntheticResult, tmp_path: Path) -> None:
    """C3: an earlier run's adjustment is stripped and the gap re-measured, so runs cannot stack past the threshold."""
    misc = cell_of(annual, "note.other_expenses.misc", "CY")
    cap = cell_of(annual, "n611.oa.nc.capadv", "CY")
    misc_formula = cell_at(annual.path, *misc).formula
    cap_printed = Decimal(str(cell_at(annual.path, *cap).value)).quantize(PAISA)
    # the true gap is 0.21; a previous fix left -0.15 on the designated line, so only 0.06 shows
    src = resave(annual.path, tmp_path / "stacked.xlsx", values={cap: float(cap_printed + Decimal("0.21"))},
                 formulas={misc: f"{misc_formula}-0.15"})
    before = api.check_workbook(src, CFG)
    assert only(before, "BAL-01").difference == Decimal("0.06")
    plan = api.propose_fixes(src, CFG, check_result=before)
    bs = adjustment(plan, "balance_sheet", "CY")
    assert (bs.status, bs.gap, bs.adjustment) == ("too_large", Decimal("0.21"), Decimal(0))
    (strip,) = [e for e in plan.edits if e.group == EditGroup.BALANCING]
    assert ((strip.sheet, strip.cell), strip.new_formula) == (misc, misc_formula)  # removed, not topped up to -0.21
    out = api.apply_fixes(src, plan, tmp_path / "corrected.xlsx", config=CFG)
    assert out.ok, out.message
    after = only(api.check_workbook(out.out_path or "", CFG), "BAL-01")
    assert (after.difference, after.fix_kind) == (Decimal("0.21"), FixKind.MANUAL)


CF_BANDS = [  # gap on both years' closing cash -> plan status per year
    ("0.15", {"PY": "applied"}),  # rounding: the PY line absorbs it and the CY opening cash follows
    ("0.25", {"PY": "too_large", "CY": "too_large"}),
    ("0.30", {"PY": "investigate", "CY": "investigate"}),  # 0.30-0.40: the exchange difference on cash is missing
    ("0.40", {"PY": "investigate", "CY": "investigate"}),
    ("0.41", {"PY": "too_large", "CY": "too_large"}),
]


@pytest.mark.parametrize(("gap", "statuses"), CF_BANDS)
def test_c3_cash_flow_gap_bands(annual: SyntheticResult, tmp_path: Path, gap: str, statuses: dict[str, str]) -> None:
    """C3: cash-flow gaps up to the threshold are balanced; 0.30-0.40 is reported as a likely missing FX line."""
    src = typed_above(annual, tmp_path / f"cf-gap-{gap}.xlsx", "cf.opening_cash", "PY", gap)
    check = api.check_workbook(src, CFG)
    rounding = Decimal(gap) <= CFG.policy.balancing_threshold
    bal02 = {f.period: f for f in check.findings if f.rule_id == "BAL-02"}
    assert {p: (f.difference, f.fix_kind) for p, f in bal02.items()} == {
        p: (Decimal(gap), FixKind.AUTO if rounding else FixKind.MANUAL) for p in ("CY", "PY")}
    assert all(("exchange difference" in f.explanation) == ("investigate" in statuses.values()) for f in bal02.values())
    plan = api.propose_fixes(src, CFG, check_result=check)
    assert {b.period: b.status for b in plan.balancing if b.statement == "cash_flow"} == statuses
    assert balancing_edits(plan) == ([cell_of(annual, "cf.ocl", "PY")] if rounding else [])


def test_c3_cash_flow_fx_sized_gap_is_reported_never_plugged(annual: SyntheticResult, tmp_path: Path) -> None:
    """C3: a 0.35 cash-flow gap (exchange difference on cash missing) is reported for both years, never plugged."""
    src = typed_above(annual, tmp_path / "cf-gap-0.35.xlsx", "cf.opening_cash", "PY", "0.35")
    check = api.check_workbook(src, CFG)
    found = [f for f in check.findings if f.rule_id == "BAL-02"]
    assert sorted((f.period, f.difference, f.fix_kind) for f in found) == [
        ("CY", Decimal("0.35"), FixKind.MANUAL), ("PY", Decimal("0.35"), FixKind.MANUAL)]
    assert all("exchange difference" in f.explanation and "never plug" in f.explanation for f in found)
    plan = api.propose_fixes(src, CFG, check_result=check)
    cf = [adjustment(plan, "cash_flow", p) for p in ("PY", "CY")]
    assert [(b.status, b.target, b.adjustment) for b in cf] == [("investigate", None, Decimal(0))] * 2
    assert all("never plugged" in b.note for b in cf)
    assert balancing_edits(plan) == []
    assert cell_of(annual, "cf.ocl", "PY") not in {(e.sheet, e.cell) for e in plan.edits}
    out = api.apply_fixes(src, plan, tmp_path / "corrected.xlsx", config=CFG)
    assert out.ok, out.message
    after = [f for f in api.check_workbook(out.out_path or "", CFG).findings if f.rule_id == "BAL-02"]
    assert sorted((f.period, f.difference, f.fix_kind) for f in after) == sorted((f.period, f.difference, f.fix_kind) for f in found)


def test_c3_translation_gap_is_reported_not_plugged() -> None:
    """C3 on the SPFS translation error (B-T02): the INR Balance Sheet is out by far more than rounding."""
    res = cached_workbook(seed=7, layout="spfs", inject=("translation_gap",))
    check = api.check_workbook(res.path, SPFS)
    bal = only(check, "BAL-01")
    assert bal.fix_kind == FixKind.MANUAL and abs(bal.difference or 0) > SPFS.policy.balancing_threshold
    assert only(check, "MULTI-04").fix_kind == FixKind.MANUAL
    plan = api.propose_fixes(res.path, SPFS, check_result=check)
    assert {(b.statement, b.period): b.status for b in plan.balancing} == {
        ("balance_sheet", "CY"): "too_large", ("cash_flow", "CY"): "too_large"}
    assert balancing_edits(plan) == []


# ============================================================================= C5 post-write verification


@dataclass
class FixedCopy:
    res: SyntheticResult
    out: Path
    plan: FixPlan
    applied: list[Edit]
    print_sheets: list[str]

    @property
    def src(self) -> Path:
        return self.res.path


@pytest.fixture(scope="module")
def fixed(tmp_path_factory: pytest.TempPathFactory) -> FixedCopy:
    """A typed -0.01 plug in the P&L total expenses, fixed on a verified copy (the C5 baseline)."""
    res = cached_workbook(seed=7, inject=("plug_total_minus",))
    check = api.check_workbook(res.path, CFG)
    plan = api.propose_fixes(res.path, CFG, check_result=check)
    out = tmp_path_factory.mktemp("c5") / "corrected.xlsx"
    result = api.apply_fixes(res.path, plan, out, config=CFG)
    assert result.ok, result.message
    return FixedCopy(res, out, plan, [e for e in plan.edits if e.id in result.applied_edit_ids], check.run.print_sheets)


def steps(report: VerificationReport) -> dict[str, bool]:
    return {s.name: s.ok for s in report.steps}


def test_c5_unplanned_figure_change_fails_verification(fixed: FixedCopy, tmp_path: Path) -> None:
    """C5: a copy that still casts, ties and re-evaluates is rejected when a cell outside the plan differs."""
    assert diff_against_source(fixed.src, fixed.out, fixed.applied) == []
    assert verify_copy(fixed.src, fixed.out, fixed.plan, fixed.applied, CFG, libreoffice=False).ok
    # 0.50 silently moved between two typed previous-year cash-flow lines: totals, ties and cached values unchanged
    tr, ofa = cell_of(fixed.res, "cf.wc_tr", "PY"), cell_of(fixed.res, "cf.wc_ofa", "PY")
    a, b = cell_at(fixed.out, *tr), cell_at(fixed.out, *ofa)
    assert a.formula is None and b.formula is None
    patcher = XlsxPatcher(fixed.out)
    patcher.set_value(*tr, a.value + 0.5)
    patcher.set_value(*ofa, b.value - 0.5)
    tampered = patcher.write(tmp_path / "tampered.xlsx")
    problems = diff_against_source(fixed.src, tampered, fixed.applied)
    assert sorted(problems) == sorted(f"{s}!{c}: constant changed" for s, c in (tr, ofa))
    report = verify_copy(fixed.src, tampered, fixed.plan, fixed.applied, CFG, libreoffice=False)
    assert steps(report) == {"xml": True, "openpyxl": True, "diff": False, "selftest": True, "recheck": True}
    assert not report.ok and sorted(report.unexpected_changes) == sorted(problems)


def test_c5_unplanned_formula_and_visibility_changes_are_reported(fixed: FixedCopy, tmp_path: Path) -> None:
    """C5: formulas, hidden rows / columns and new cells outside the plan are differences; planned hides are not."""
    sheet, row, cols = fixed.res.index["bs.cash"]
    cy, py_col = make_addr(row, int(cols["CY"])), int(cols["PY"])
    cash = cell_at(fixed.out, sheet, cy)
    with Workbook.open(fixed.out) as wb:
        free = make_addr(1, wb.sheet(sheet).max_col + 2)  # outside the used range
    patcher = XlsxPatcher(fixed.out)
    patcher.set_formula(sheet, cy, f"{cash.formula}+0", cash.value)  # same figure, different formula
    patcher.hide_row(sheet, row)
    patcher.hide_col(sheet, py_col)
    patcher.set_value(sheet, free, 1.0)
    tampered = patcher.write(tmp_path / "tampered.xlsx")
    content = [f"{sheet}!{cy}: formula changed", f"{sheet}!{free}: cell added/removed"]
    hidden = [f"{sheet}: hidden rows changed", f"{sheet}: hidden columns changed"]
    assert sorted(diff_against_source(fixed.src, tampered, fixed.applied)) == sorted(content + hidden)
    hides = [Edit(sheet=sheet, cell=str(row), kind=EditKind.HIDE_ROW, group=EditGroup.HIDE),
             Edit(sheet=sheet, cell=index_to_col(py_col), kind=EditKind.HIDE_COL, group=EditGroup.HIDE)]
    assert sorted(diff_against_source(fixed.src, tampered, fixed.applied + hides)) == sorted(content)


def rewrite_part(src: Path, dest: Path, part: str, edit: Callable[[bytes], bytes]) -> Path:
    """Copy of the package with one part's bytes rewritten and every other part copied byte for byte."""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zout:
        assert part in zin.namelist()
        for info in zin.infolist():
            data = zin.read(info.filename)
            zout.writestr(info, edit(data) if info.filename == part else data)
    return dest


def untouched_sheets(fixed: FixedCopy) -> set[str]:
    changed, _added = changed_sheet_parts(fixed.src, fixed.out)
    with Workbook.open(fixed.out) as wb:
        return {s.name for s in wb.sheets if s.part not in changed}


def retext_shared_string(fixed: FixedCopy, dest: Path) -> tuple[Path, str]:
    """Restate the text of one cell on a print sheet the fix never touched, in the shared-string table only."""
    untouched = untouched_sheets(fixed) & set(fixed.print_sheets)
    with Workbook.open(fixed.out) as wb:
        uses = Counter(c.value for s in wb.sheets for c in s.iter_cells() if isinstance(c.value, str))
        sheet, addr, text = next((s.name, c.addr, c.value) for s in wb.sheets if s.name in untouched for c in s.iter_cells()
                                 if isinstance(c.value, str) and uses[c.value] == 1 and re.fullmatch(r"\w[\w .,()-]*\w", c.value))
    old, new = f"<t>{text}</t>".encode(), f"<t>{text} (restated)</t>".encode()

    def edit(xml: bytes) -> bytes:
        assert xml.count(old) == 1
        return xml.replace(old, new)

    return rewrite_part(fixed.out, dest, "xl/sharedStrings.xml", edit), f"{sheet}!{addr}: constant changed"


def reformat_style(fixed: FixedCopy, dest: Path) -> tuple[Path, str]:
    """Change the number format of a cell style used only on sheets the fix never touched, in the style table only."""
    untouched = untouched_sheets(fixed)
    with Workbook.open(fixed.out) as wb:
        elsewhere = {c.style for s in wb.sheets if s.name not in untouched for c in s.iter_cells()}
        sheet, addr, style = next((s.name, c.addr, c.style) for s in wb.sheets if s.name in untouched for c in s.iter_cells()
                                  if c.is_number and c.style not in elsewhere)

    def edit(xml: bytes) -> bytes:
        root = etree.fromstring(xml)
        xf = root.find(q("cellXfs")).findall(q("xf"))[style]
        xf.set("numFmtId", "1" if xf.get("numFmtId") != "1" else "2")  # '0' instead of the 2-dp format
        xf.set("applyNumberFormat", "1")
        return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)

    return rewrite_part(fixed.out, dest, "xl/styles.xml", edit), f"{sheet}!{addr}: number format changed"


@pytest.mark.parametrize("tamper", [retext_shared_string, reformat_style], ids=["shared-strings", "styles"])
def test_c5_changes_outside_the_sheet_parts_are_reported(fixed: FixedCopy, tmp_path: Path,
                                                         tamper: Callable[[FixedCopy, Path], tuple[Path, str]]) -> None:
    """C5: text and number formats live in shared parts; a byte-identical sheet part does not prove its cells unchanged."""
    tampered, problem = tamper(fixed, tmp_path / "tampered.xlsx")
    assert problem in diff_against_source(fixed.src, tampered, fixed.applied)
    report = verify_copy(fixed.src, tampered, fixed.plan, fixed.applied, CFG, libreoffice=False)
    assert not report.ok and not steps(report)["diff"] and problem in report.unexpected_changes


def test_c5_failed_verification_deletes_copy_and_restores_previous_output(fixed: FixedCopy, tmp_path: Path,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """C5: if the written copy differs outside the plan it is deleted, and an earlier output is put back from its backup."""
    real_write = XlsxPatcher.write

    def write_with_stray_edit(self: XlsxPatcher, dest: Path) -> Path:
        self.set_text("BS", "A1", "Corvus Data Services Ltd")  # a cell no edit of the plan touches
        return real_write(self, dest)

    monkeypatch.setattr(XlsxPatcher, "write", write_with_stray_edit)
    out = tmp_path / "corrected.xlsx"
    first = api.apply_fixes(fixed.src, fixed.plan, out, config=CFG)
    assert not first.ok and first.out_path is None and "verification failed" in first.message
    assert not steps(first.verification)["diff"] and "BS!A1: constant changed" in first.verification.unexpected_changes
    assert not out.exists()

    shutil.copyfile(fixed.out, out)  # an earlier, verified output at the same path
    previous = out.read_bytes()
    second = api.apply_fixes(fixed.src, fixed.plan, out, config=CFG, backup_dir=tmp_path / "backups")
    assert not second.ok and second.backup_path is not None
    assert out.read_bytes() == previous


# ============================================================================= C11 period scope


@dataclass
class ScopeCase:
    res: SyntheticResult
    check: CheckResult
    plan: FixPlan


@pytest.fixture(scope="module")
def scope_case() -> ScopeCase:
    """SPFS workbook with a stale Dec-25 Balance Sheet column (safe to hide) and one sharing a column with ageing buckets."""
    res = cached_workbook(seed=7, layout="spfs", inject=("stale_dec_column", "unsafe_hide_column"))
    check = api.check_workbook(res.path, SPFS)
    return ScopeCase(res, check, api.propose_fixes(res.path, SPFS, check_result=check))


def test_c11_safe_out_of_scope_column_is_hidden_once_approved(scope_case: ScopeCase, tmp_path: Path) -> None:
    """C11: the stale column becomes a hide approval item; approved and applied, the copy verifies and MULTI-01 is gone."""
    expected = next(e for e in scope_case.res.expected if e.rule_id == "MULTI-01" and e.sheet == "BS")
    _row, col = split_addr(expected.cells[0])
    finding = next(f for f in scope_case.check.findings if f.rule_id == "MULTI-01" and f.sheet == "BS")
    assert finding.cells == expected.cells and finding.fix_kind == FixKind.APPROVAL
    hides = [e for e in scope_case.plan.edits if e.kind in (EditKind.HIDE_COL, EditKind.HIDE_ROW)]
    assert [(e.sheet, e.cell, e.kind, e.group, e.fix_kind) for e in hides] == [
        ("BS", index_to_col(col), EditKind.HIDE_COL, EditGroup.HIDE, FixKind.APPROVAL)]
    (hide,) = hides
    assert hide.finding_ids == [finding.id]

    unapproved = api.apply_fixes(scope_case.res.path, scope_case.plan, tmp_path / "unapproved.xlsx", config=SPFS)
    assert unapproved.ok and hide.id in unapproved.skipped_edit_ids
    with Workbook.open(unapproved.out_path or "") as wb:
        assert not wb.sheet("BS").col_hidden(col)

    approved = api.apply_fixes(scope_case.res.path, scope_case.plan, tmp_path / "approved.xlsx", config=SPFS,
                               approved_edit_ids={hide.id})
    assert approved.ok, approved.message
    assert hide.id in approved.applied_edit_ids
    done = steps(approved.verification)
    assert all(done.values()) and {"xml", "openpyxl", "diff", "selftest", "recheck"} <= set(done)
    with Workbook.open(approved.out_path or "") as wb:
        assert wb.sheet("BS").col_hidden(col)
    after = api.check_workbook(approved.out_path or "", SPFS)
    remaining = sorted((f.rule_id, f.sheet or "", f.cells) for f in after.findings)
    assert remaining == sorted((f.rule_id, f.sheet or "", f.cells) for f in scope_case.check.findings if f is not finding)


def test_c11_unsafe_column_is_never_hidden(scope_case: ScopeCase, tmp_path: Path) -> None:
    """C11 / MULTI-02: a stale column sharing its letter with ageing buckets is never proposed, so never hidden."""
    unsafe = next(e for e in scope_case.res.expected if e.rule_id == "MULTI-02")
    sheet, letter = unsafe.sheet, unsafe.cells[0]
    m1 = next(f for f in scope_case.check.findings if f.rule_id == "MULTI-01" and f.sheet == sheet)
    m2 = next(f for f in scope_case.check.findings if f.rule_id == "MULTI-02" and f.sheet == sheet)
    assert m1.fix_kind == FixKind.MANUAL and f"Do not hide column {letter}" in (m1.suggested_fix or "")
    assert m2.cells[0] == letter and "ageing / maturity buckets" in m2.explanation
    assert (sheet, letter) not in {(e.sheet, e.cell) for e in scope_case.plan.edits if e.kind == EditKind.HIDE_COL}

    everything = {e.id for e in scope_case.plan.edits}  # every approval item approved
    out = api.apply_fixes(scope_case.res.path, scope_case.plan, tmp_path / "all-approved.xlsx", config=SPFS,
                          approved_edit_ids=everything)
    assert out.ok, out.message
    with Workbook.open(out.out_path or "") as wb:
        assert not wb.sheet(sheet).col_hidden(col_to_index(letter))
    after = api.check_workbook(out.out_path or "", SPFS)
    assert {f.rule_id for f in after.findings if f.sheet == sheet and f.rule_id.startswith("MULTI")} == {"MULTI-01", "MULTI-02"}


# ============================================================================= E-ITGC-02 rule-change regression

PACKAGED_RULES = resources.files("fscore.rules").joinpath("rules.yaml")


@pytest.fixture
def rule_catalogue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A copy of the rule catalogue that the registry reads instead of the packaged file (cache reset around the test)."""
    registry.load_all_checks()  # every check registers against the packaged catalogue first
    folder = tmp_path / "rules"
    folder.mkdir()
    catalogue = folder / "rules.yaml"
    catalogue.write_bytes(PACKAGED_RULES.read_bytes())

    def files(package: str) -> Path:
        assert package == "fscore.rules"
        return folder

    monkeypatch.setattr(registry, "resources", SimpleNamespace(files=files))
    registry._load.cache_clear()
    yield catalogue
    registry._load.cache_clear()  # monkeypatch is undone after this, so the next call reloads the packaged file


def edit_rule(catalogue: Path, rule_id: str, old: str, new: str) -> None:
    """Change one rule's entry in the catalogue copy; nothing else in the file moves."""
    lines = catalogue.read_text(encoding="utf-8").splitlines(keepends=True)
    (i,) = [n for n, line in enumerate(lines) if f"{{id: {rule_id}," in line]
    assert lines[i].count(old) == 1, lines[i]
    lines[i] = lines[i].replace(old, new)
    catalogue.write_text("".join(lines), encoding="utf-8")
    registry._load.cache_clear()


def test_e_itgc_02_rule_set_hash_changes_with_any_rule_edit(rule_catalogue: Path) -> None:
    """E-ITGC-02: the hash identifies the exact rule text, so an edit shows even when no version is bumped."""
    packaged = PACKAGED_RULES.read_text(encoding="utf-8")
    version, digest = registry.rule_set_version(), registry.rule_set_hash()
    assert digest == hashlib.sha256(packaged.encode("utf-8")).hexdigest()  # identical copy, identical hash
    edit_rule(rule_catalogue, "BAL-01", "severity: High", "severity: Medium")
    assert registry.get_rule("BAL-01").severity == Severity.MEDIUM  # the registry now runs on the edited catalogue
    assert registry.rule_set_version() == version  # nobody bumped the rule-set version ...
    assert registry.rule_set_hash() != digest  # ... yet the recorded hash changes
    edit_rule(rule_catalogue, "BAL-01", "severity: Medium", "severity: High")
    assert registry.rule_set_hash() == digest  # content-addressed: the original text gives the original hash


def test_e_itgc_02_every_run_records_the_rule_set_it_ran_with(rule_catalogue: Path) -> None:
    """E-ITGC-02: each run records rule-set version and hash, so a changed finding is traceable to the rule change."""
    res = cached_workbook(seed=7, inject=("bs_not_tally",))
    before = api.check_workbook(res.path, CFG)
    assert (before.run.rule_set_version, before.run.rule_set_hash) == (registry.rule_set_version(), registry.rule_set_hash())

    edit_rule(rule_catalogue, "BAL-01", "severity: High", 'severity: Medium, version: "2"')
    after = api.check_workbook(res.path, CFG)
    assert (after.run.rule_set_version, after.run.rule_set_hash) == (registry.rule_set_version(), registry.rule_set_hash())
    # same file, configuration and engine: the recorded rule set is the only difference between the runs ...
    same = ("file_sha256", "config_hash", "engine_version", "rule_set_version")
    assert [getattr(after.run, k) for k in same] == [getattr(before.run, k) for k in same]
    assert after.run.rule_set_hash != before.run.rule_set_hash
    # ... and it explains the changed finding
    assert [(f.severity, f.rule_version) for f in before.findings if f.rule_id == "BAL-01"] == [(Severity.HIGH, "1")]
    assert [(f.severity, f.rule_version) for f in after.findings if f.rule_id == "BAL-01"] == [(Severity.MEDIUM, "2")]
