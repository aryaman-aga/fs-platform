"""TXT-01…05 and TXT-07 on hand-built statement sets (one injected error per test)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from helpers_checks_t import clean_spec, run_rules, write

from fscore.config import EngagementConfig
from fscore.models import Severity
from fscore.prepare.spec import WorkbookSpec


def find(spec: WorkbookSpec, sheet: str, text: str, nth: int = 0) -> tuple[int, int]:
    hits = sorted((r, c) for (r, c), cs in spec.sheet(sheet).cells.items() if isinstance(cs.value, str) and cs.value == text)
    return hits[nth]


def run(tmp_path: Path, spec: WorkbookSpec, prefixes: tuple[str, ...] = ("TXT-",), config: EngagementConfig | None = None):
    p = write(spec, tmp_path / "wb.xlsx")
    return run_rules(p, prefixes, config)


def rules(ctx, rule_id: str) -> list:
    return [f for f in ctx.findings if f.rule_id == rule_id]


def test_clean_set_has_no_txt_or_multi_findings(tmp_path: Path) -> None:
    ctx = run(tmp_path, clean_spec(), ("TXT-01", "TXT-02", "TXT-03", "TXT-04", "TXT-05", "TXT-07", "MULTI-"))
    assert [(f.rule_id, f.sheet, f.cells, f.explanation) for f in ctx.findings] == []


# ---------------------------------------------------------------- TXT-01


def test_din_placeholder_is_high(tmp_path: Path) -> None:
    spec = clean_spec()
    r, c = find(spec, "PL", "DIN: 07654321")
    spec.sheet("PL").set(r, c, "DIN: XXX")
    ctx = run(tmp_path, spec)
    hits = [f for f in rules(ctx, "TXT-01") if f.sheet == "PL" and "placeholder" in f.explanation]
    assert hits and hits[0].severity == Severity.HIGH and hits[0].cells == [f"D{r}"]


def test_director_b_shows_director_a_din(tmp_path: Path) -> None:
    spec = clean_spec()
    r, c = find(spec, "CFS", "DIN: 07654321")
    spec.sheet("CFS").set(r, c, "DIN: 01234567")
    ctx = run(tmp_path, spec)
    hits = [f for f in rules(ctx, "TXT-01") if f.sheet == "CFS" and f.cells == [f"D{r}"]]
    assert hits and hits[0].severity == Severity.HIGH
    assert "Asha Raman" in hits[0].explanation and "01234567" in hits[0].explanation


def test_two_people_share_a_din_on_the_balance_sheet(tmp_path: Path) -> None:
    spec = clean_spec()
    r, c = find(spec, "BS", "DIN: 07654321")
    spec.sheet("BS").set(r, c, "DIN: 01234567")
    ctx = run(tmp_path, spec)
    assert any("same DIN" in f.explanation and f.sheet == "BS" for f in rules(ctx, "TXT-01"))


def test_invalid_din_format(tmp_path: Path) -> None:
    spec = clean_spec()
    r, c = find(spec, "SOCIE", "DIN: 01234567")
    spec.sheet("SOCIE").set(r, c, "DIN: 1234567")
    ctx = run(tmp_path, spec)
    assert any("8-digit" in f.explanation and f.cells == [f"C{r}"] for f in rules(ctx, "TXT-01"))


def test_name_variant_reported_not_fixed(tmp_path: Path) -> None:
    spec = clean_spec()
    r, c = find(spec, "CFS", "Asha Raman")
    spec.sheet("CFS").set(r, c, "Asha Ramam", style="sign_bold")
    ctx = run(tmp_path, spec)
    hits = [f for f in rules(ctx, "TXT-01") if "spelling variant" in f.explanation]
    assert hits and hits[0].sheet == "CFS" and hits[0].severity == Severity.MEDIUM and hits[0].expected == "Asha Raman"


def test_signing_dates(tmp_path: Path) -> None:
    spec = clean_spec()
    for sheet, new in (("SOCIE", "Date: May 16, 2026"), ("CFS", "Date: March 30, 2026"), ("Notes", "Date: XXXXXX")):
        r, c = find(spec, sheet, "Date: May 15, 2026", 1)
        spec.sheet(sheet).set(r, c, new)
    ctx = run(tmp_path, spec)
    txt = [(f.sheet, f.explanation) for f in rules(ctx, "TXT-01")]
    assert any(s == "SOCIE" and "differs" in e for s, e in txt)
    assert any(s == "CFS" and "not after the period end" in e for s, e in txt)
    assert any(s == "Notes" and "placeholder" in e for s, e in txt)


def test_statement_with_blank_date(tmp_path: Path) -> None:
    spec = clean_spec()
    for nth in (0, 1):
        r, c = find(spec, "PL", "Date: May 15, 2026", 0)
        spec.sheet("PL").cells.pop((r, c))
        _ = nth
    ctx = run(tmp_path, spec)
    assert any(f.sheet == "PL" and "no signing date" in f.explanation and f.severity == Severity.HIGH for f in rules(ctx, "TXT-01"))


def test_cin_and_frn(tmp_path: Path) -> None:
    spec = clean_spec()
    r, c = find(spec, "CFS", "CIN: U72900KA2015PTC081234")
    spec.sheet("CFS").set(r, c, "CIN: U72900KA2015PTC081235")
    r, c = find(spec, "PL", "Firm Registration No.: 123456W")
    spec.sheet("PL").set(r, c, "Firm Registration No.: 12345W")
    ctx = run(tmp_path, spec)
    msgs = [(f.sheet, f.explanation) for f in rules(ctx, "TXT-01")]
    assert any(s == "CFS" and "CIN" in e and "differs" in e for s, e in msgs)
    assert any(s == "PL" and "Firm Registration" in e and "format" in e for s, e in msgs)


def test_membership_mismatch_and_missing_place(tmp_path: Path) -> None:
    spec = clean_spec()
    r, c = find(spec, "SOCIE", "Membership No.: 234567")
    spec.sheet("SOCIE").set(r, c, "Membership No.: 234568")
    for nth in (1, 0):
        rr, cc = find(spec, "PL", "Place: Bengaluru", nth)
        spec.sheet("PL").cells.pop((rr, cc))
    ctx = run(tmp_path, spec)
    msgs = [(f.sheet, f.explanation) for f in rules(ctx, "TXT-01")]
    assert any(s == "SOCIE" and "membership no." in e.lower() and "differs" in e for s, e in msgs)
    assert any(s == "PL" and "no place of signing" in e for s, e in msgs)


def test_misspelled_designations(tmp_path: Path) -> None:
    spec = clean_spec()
    r, c = find(spec, "PL", "Director", 1)
    spec.sheet("PL").set(r, c, "Diretor")
    r, c = find(spec, "SOCIE", "Chartered Accountants")
    spec.sheet("SOCIE").set(r, c, "Charterd Accountants")
    ctx = run(tmp_path, spec)
    msgs = [(f.sheet, f.explanation) for f in rules(ctx, "TXT-01")]
    assert any(s == "PL" and "'Diretor'" in e for s, e in msgs)
    assert any(s == "SOCIE" and "'Charterd'" in e for s, e in msgs)
    # the misspelled block is still parsed: no spurious DIN findings for it
    assert not any(s == "PL" and "DIN" in e for s, e in msgs)


def test_signatory_master_file(tmp_path: Path) -> None:
    master = tmp_path / "signatories.yaml"
    master.write_text("signatories:\n  - {name: Asha Raman, role: Director, din: '01234567'}\n  - {name: Vikram Sethi, role: Director, din: '07654320'}\n")
    ctx = run(tmp_path, clean_spec(), config=EngagementConfig(signatories=str(master)))
    hits = [f for f in rules(ctx, "TXT-01") if "master file" in f.explanation]
    assert hits and all("Vikram Sethi" in f.explanation for f in hits)


# ---------------------------------------------------------------- TXT-02


def test_stale_statement_heading(tmp_path: Path) -> None:
    spec = clean_spec()
    spec.sheet("BS").set(2, 1, "Balance Sheet as at March 31, 2025", style="subtitle")
    ctx = run(tmp_path, spec)
    hits = [f for f in rules(ctx, "TXT-02") if f.sheet == "BS" and f.cells == ["A2"]]
    assert hits and hits[0].severity == Severity.HIGH


def test_as_at_heading_on_profit_and_loss(tmp_path: Path) -> None:
    spec = clean_spec()
    spec.sheet("PL").set(5, 3, "As at March 31, 2026", style="header")
    ctx = run(tmp_path, spec)
    assert any(f.sheet == "PL" and f.cells == ["C5"] and "As at" in f.explanation for f in rules(ctx, "TXT-02"))


def test_stale_comparative_in_caption_and_malformed_date(tmp_path: Path) -> None:
    spec = clean_spec()
    spec.sheet("Notes").set(42, 1, "Sale of services (March 31, 2024: ₹ 700.00 lakhs)")
    spec.sheet("Notes").set(38, 1, "Refer note dated 31st April 2026.")
    ctx = run(tmp_path, spec)
    msgs = [(f.cells, f.explanation) for f in rules(ctx, "TXT-02")]
    assert any(c == ["A42"] and "March 31, 2024" in e for c, e in msgs)
    assert any(c == ["A38"] and "invalid calendar date" in e for c, e in msgs)


def test_duplicated_period_heading(tmp_path: Path) -> None:
    spec = clean_spec()
    spec.sheet("Notes").set(25, 4, "As at March 31, 2026", style="header")  # PY heading copied from CY
    ctx = run(tmp_path, spec)
    assert any("same heading" in f.explanation and f.sheet == "Notes" for f in rules(ctx, "TXT-02"))


# ---------------------------------------------------------------- TXT-03


def test_note_numbering(tmp_path: Path) -> None:
    spec = clean_spec()
    spec.sheet("BS").set(15, 2, 11, style="note_ref")  # 'Trade payables' now points at a missing Note 11
    spec.sheet("Notes").set(39, 1, "7. Trade payables", style="section")  # Note 7 used twice
    spec.sheet("Notes").set(20, 1, "(a) Credit impaired")  # (a) twice within Note 4
    ctx = run(tmp_path, spec)
    msgs = [f.explanation for f in rules(ctx, "TXT-03")]
    assert any("Note 11 is referenced" in m for m in msgs)
    assert any("Note number 7 is used for more than one note" in m for m in msgs)
    assert any("'(a)' is used twice within Note 4" in m for m in msgs)


# ---------------------------------------------------------------- TXT-04


def test_captions_vs_signs(tmp_path: Path) -> None:
    spec = clean_spec()
    cf = spec.sheet("CFS")
    cf.set(11, 3, -25.00, style="amount")  # Proceeds shown as an outflow
    cf.set(12, 4, 12.00, style="amount")  # Repayment shown as an inflow
    nt = spec.sheet("Notes")
    nt.set(21, 3, 12.10, style="amount")  # 'Less:' row positive and added
    nt.set(14, 1, "Additions", style="caption_bold")  # closing row labelled as a movement
    ctx = run(tmp_path, spec)
    msgs = [(f.sheet, f.cells, f.explanation) for f in rules(ctx, "TXT-04")]
    assert any(s == "CFS" and c == ["A11", "C11"] and "outflow" in e for s, c, e in msgs)
    assert any(s == "CFS" and c == ["A12", "D12"] and "inflow" in e for s, c, e in msgs)
    assert any(s == "Notes" and "C21" in c and "Less" in e for s, c, e in msgs)
    assert any(s == "Notes" and "closing balance" in e for s, c, e in msgs)


# ---------------------------------------------------------------- TXT-05


def test_units(tmp_path: Path) -> None:
    spec = clean_spec()
    spec.sheet("PL").cells.pop((3, 1))
    spec.sheet("CFS").set(3, 1, "(All amounts in ₹ millions, unless otherwise stated)", style="unit")
    ctx = run(tmp_path, spec)
    msgs = [(f.sheet, f.explanation) for f in rules(ctx, "TXT-05")]
    assert any(s == "PL" and "No unit line" in e for s, e in msgs)
    assert any(s == "CFS" and "millions" in e for s, e in msgs)


def test_rupee_sized_figures_in_lakhs_table(tmp_path: Path) -> None:
    spec = clean_spec()
    nt = spec.sheet("Notes")
    for r, v in ((45, 60_010_000.0), (46, 125_000.0)):
        nt.set(r, 3, v, style="amount")
        nt.set(r, 4, v * 0.9 // 1, style="amount")
    ctx = run(tmp_path, spec)
    assert any("look like rupees" in f.explanation for f in rules(ctx, "TXT-05"))


# ---------------------------------------------------------------- TXT-07


def test_number_format_inconsistencies(tmp_path: Path) -> None:
    spec = clean_spec()
    nt = spec.sheet("Notes")
    nt.set(26, 3, 1055.30, style="amount_plain")  # 0.00 without thousands separator
    nt.set(27, 3, 0.45, style="amount_0dp")  # 0-decimal figure beside 2-decimal ones
    nt.set(9, 4, 0, style="amount_plain")  # zero shown as 0.00 in an accounting table
    nt.set(19, 4, 180.2345, style="text")  # General format on an amount
    ctx = run(tmp_path, spec)
    msgs = [(f.cells, f.explanation) for f in rules(ctx, "TXT-07")]
    # one finding per column, listing every offending cell
    assert any(c == ["C26", "C27"] and "thousands separators" in e and "decimal" in e for c, e in msgs)
    assert any("D9" in c and "zero" in e.lower() for c, e in msgs)
    assert any("D19" in c and "General" in e for c, e in msgs)


def test_mixed_negative_styles(tmp_path: Path) -> None:
    spec = clean_spec()
    from fscore.prepare import style as st

    st.STYLES["amount_minus"] = {"num_format": "#,##0.00;-#,##0.00;\"-\""}
    try:
        spec.sheet("Notes").set(10, 3, -4.00, style="amount_minus")
        ctx = run(tmp_path, spec)
    finally:
        st.STYLES.pop("amount_minus", None)
    assert any("brackets and with a minus" in f.explanation for f in rules(ctx, "TXT-07"))


@pytest.mark.parametrize("rule", ["TXT-01", "TXT-02", "TXT-03", "TXT-04", "TXT-05", "TXT-07"])
def test_rules_are_registered(rule: str) -> None:
    from fscore.registry import implementations, load_all_checks

    load_all_checks()
    assert any(rule in impl.rule_ids for impl in implementations())


def test_blank_date_next_to_another_signatory_date(tmp_path: Path) -> None:
    spec = clean_spec()
    r, c = find(spec, "BS", "Date: May 15, 2026", 0)  # partner's date in column A; directors' date sits to its right
    spec.sheet("BS").set(r, c, "Date:")
    ctx = run(tmp_path, spec)
    assert any(f.sheet == "BS" and f.cells == [f"A{r}"] and "placeholder" in f.explanation for f in rules(ctx, "TXT-01"))


def test_item_lists_restart_but_repeats_are_reported(tmp_path: Path) -> None:
    spec = clean_spec()
    nt = spec.sheet("Notes")
    nt.set(19, 1, "(b) Unsecured, considered good")  # list starts at (b) …
    nt.set(20, 1, "(b) Credit impaired")  # … and repeats it
    ctx = run(tmp_path, spec)
    hits = [f for f in rules(ctx, "TXT-03") if "used twice" in f.explanation]
    assert len(hits) == 1 and hits[0].cells == ["A19", "A20"]
    # separate lists that each start at (i) are fine (Note 5 already uses (i)/(ii); add a second list)
    spec2 = clean_spec()
    spec2.sheet("Notes").set(45, 1, "(i) Legal and professional fees")
    spec2.sheet("Notes").set(46, 1, "(ii) Miscellaneous expenses")
    spec2.sheet("Notes").set(26, 1, "(i) Balances with banks in current accounts")
    ctx2 = run(tmp_path, spec2)
    assert not rules(ctx2, "TXT-03")


def test_general_and_plain_formats_reported_in_one_column_finding(tmp_path: Path) -> None:
    spec = clean_spec()
    nt = spec.sheet("Notes")
    nt.set(45, 3, 600.10, style="amount_0dp")
    nt.set(46, 3, 1.25, style="amount_plain")  # small value: same digits, but a different (plain) format
    ctx = run(tmp_path, spec)
    hits = [f for f in rules(ctx, "TXT-07") if f.sheet == "Notes"]
    assert len(hits) == 1 and hits[0].cells == ["C45", "C46"]
