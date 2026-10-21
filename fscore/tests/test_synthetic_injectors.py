"""Every injector applies, writes a valid workbook and produces its symptom; clean stays clean."""

from __future__ import annotations

import re
from decimal import Decimal

import pytest
from helpers_synthetic import cached_workbook

from fscore.engine.refs import split_addr
from fscore.io.xlsx_reader import Workbook
from fscore.registry import get_rule
from fscore.synthetic.generator import injection_rules, list_injections, make_workbook
from fscore.synthetic.inject import registry
from fscore.synthetic.oracle import scan_casting, tie_gaps

ANNUAL = sorted(list_injections("annual"))
SPFS_ONLY = sorted(set(list_injections("spfs")) - set(ANNUAL))


def _cell_snapshot(wb: Workbook) -> dict[tuple[str, str], tuple[object, object, bool, str]]:
    out = {}
    for s in wb.sheets:
        for c in s.iter_cells():
            out[(s.name, c.addr)] = (c.formula, c.value if not isinstance(c.value, float) else round(c.value, 9), s.row_hidden(c.row), s.number_format(c))
    return out


@pytest.fixture(scope="module")
def clean_snapshot():
    with Workbook.open(cached_workbook(seed=7).path) as wb:
        return _cell_snapshot(wb)


def test_catalogue_uses_known_rules() -> None:
    for name, rules in injection_rules().items():
        for rid in rules:
            get_rule(rid)
        assert list_injections()[name]
    assert len(registry()) >= 60


@pytest.mark.parametrize("name", ANNUAL)
def test_injector_applies_with_symptom(name: str, clean_snapshot) -> None:
    r = cached_workbook(seed=7, inject=(name,))
    inj = registry()[name]
    with Workbook.open(r.path) as wb:
        snap = _cell_snapshot(wb)
        changed = {k for k in set(snap) | set(clean_snapshot) if snap.get(k) != clean_snapshot.get(k)}
        assert changed, f"{name} changed nothing"
        declared = [e for e in r.expected if e.source == "injected"]
        if name != "header_row_data":
            assert declared, f"{name} declares no finding"
        else:
            assert r.protected_cells, "header-row data must be reported as a protected cell"
        for e in declared:
            get_rule(e.rule_id)
            sheet = wb.get_sheet(e.sheet)
            assert sheet is not None, e
            for a in e.cells:
                if re.fullmatch(r"[A-Z]{1,3}", a):
                    continue  # whole column (MULTI-02)
                row, col = split_addr(a)
                if e.rule_id == "CAST-03":
                    assert sheet.cell(row, col) is None or sheet.cell(row, col).value is None, "missing total cell must be blank"
                    continue
                assert sheet.cell(row, col) is not None or row in sheet.rows_hidden, f"{name}: {e.sheet}!{a} absent"
        if inj.phase != "build" and declared:
            # the declared cells (or their row) are among the changes, or a value they depend on moved
            hit = any(
                (e.sheet, a) in changed or any(k[0] == e.sheet and re.sub(r"[A-Z]+", "", k[1]) == re.sub(r"[A-Z]+", "", a) for k in changed)
                for e in declared for a in e.cells if not re.fullmatch(r"[A-Z]{1,3}", a)
            )
            assert hit or name in ("cf_fx_gap", "ageing_vs_note"), f"{name}: declared cells unaffected"


def test_casting_family_symptoms() -> None:
    issues = {(i.sheet, i.cell) for i in scan_casting(cached_workbook(seed=7, inject=("plug_total_minus",)).path)}
    r = cached_workbook(seed=7, inject=("plug_total_minus",))
    plug = next(e for e in r.expected if e.rule_id == "PLUG-01")
    assert (plug.sheet, plug.cells[0]) in issues
    with Workbook.open(r.path) as wb:
        s, cell = plug.sheet, plug.cells[0]
        assert wb.sheet(s).cell_at(cell).formula.endswith("-0.01")
    # collateral: the BS no longer tallies by exactly the plug
    gaps = tie_gaps(r.path, r.index)
    assert abs(gaps["bs"]["CY"]) == Decimal("0.01")


def test_hidden_and_text_symptoms() -> None:
    r = cached_workbook(seed=7, inject=("hidden_row_in_total", "gc_hidden_factors", "din_placeholder", "white_font_working", "ref_error_hidden_row"))
    with Workbook.open(r.path) as wb:
        hid = next(e for e in r.expected if e.rule_id == "HID-01")
        sheet = wb.sheet(hid.sheet)
        assert any(sheet.row_hidden(rw) for rw in range(1, sheet.max_row + 1))
        gc = next(e for e in r.expected if e.rule_id == "GC-04")
        assert all(wb.sheet(gc.sheet).row_hidden(split_addr(a)[0]) for a in gc.cells)
        din = next(e for e in r.expected if e.rule_id == "TXT-01")
        assert wb.sheet(din.sheet).cell_at(din.cells[0]).value == "DIN: XXX"
        white = next(e for e in r.expected if e.rule_id == "HID-02" and "white" in e.note)
        c = wb.sheet(white.sheet).cell_at(white.cells[0])
        assert wb.sheet(white.sheet).font_invisible(c)
        ref = next(e for e in r.expected if e.rule_id == "HID-02" and "#REF" in e.note)
        rc = wb.sheet(ref.sheet).cell_at(ref.cells[0])
        assert rc.is_error and wb.sheet(ref.sheet).row_hidden(rc.row)


def test_all_annual_injectors_combine(tmp_path) -> None:
    r = make_workbook(tmp_path / "all.xlsx", inject=ANNUAL)
    with Workbook.open(r.path) as wb:
        assert wb.sheet_names[0] == "BS"
    rules = r.expected_rules()
    for name in ANNUAL:
        for rid in injection_rules()[name]:
            if name in ("header_row_data",):
                continue
            assert rid in rules, (name, rid)


def test_clean_unaffected_by_previous_injections(tmp_path) -> None:
    make_workbook(tmp_path / "noisy.xlsx", inject=ANNUAL[:10])
    r = make_workbook(tmp_path / "clean.xlsx")
    assert r.expected == []


def test_unknown_or_wrong_layout_injection(tmp_path) -> None:
    with pytest.raises(KeyError):
        make_workbook(tmp_path / "x.xlsx", inject=["no_such_injection"])
    with pytest.raises(ValueError):
        make_workbook(tmp_path / "y.xlsx", inject=["translation_gap"], layout="annual")


@pytest.mark.parametrize("name", SPFS_ONLY)
def test_spfs_only_injectors(name: str) -> None:
    r = cached_workbook(seed=7, layout="spfs", inject=(name,))
    assert any(e.source == "injected" for e in r.expected)
