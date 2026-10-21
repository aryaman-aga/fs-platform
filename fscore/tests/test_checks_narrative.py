"""Narrative rules NARR-01…05 and TXT-06 (outside the Going Concern note), all figures invented."""

from __future__ import annotations

from pathlib import Path

from helpers_narr import EntityOpts, build_entity, config, rules, run

from fscore.runner import build_context, run_checks


def _run(tmp_path: Path, name: str, *paras: str):  # type: ignore[no-untyped-def]
    return run(build_entity(tmp_path / f"{name}.xlsx", EntityOpts(extra_narrative=list(paras))))


def _cells(res, rule: str) -> list[str]:  # type: ignore[no-untyped-def]
    return [c for f in res.findings if f.rule_id == rule for c in f.cells]


def test_clean_entity_has_no_narrative_findings(tmp_path: Path) -> None:
    res = _run(tmp_path, "clean")
    assert rules(res, "NARR") == []
    assert rules(res, "TXT-06") == []


def test_narr01_figure_mismatch(tmp_path: Path) -> None:
    res = _run(tmp_path, "m", "Revenue from operations for the year ended March 31, 2026 amounted to ₹ 1,205.00 lakhs.")
    assert _cells(res, "NARR-01") == ["A13"]
    rec = next(r for r in res.narrative if r["cell"] == "A13")
    assert rec["status"] == "Mismatch" and rec["expected_printed"] == "1,250.00" and rec["expected_cell"] == "PL!C6"


def test_converted_units_match(tmp_path: Path) -> None:
    res = _run(tmp_path, "cr", "The Company's revenue for the year ended March 31, 2026 was ₹ 12.50 crores.")
    assert rules(res, "NARR") == []
    assert [r["status"] for r in res.narrative if r["cell"] == "A13"] == ["Matched"]


def test_narr02_claim_contradicted(tmp_path: Path) -> None:
    res = _run(tmp_path, "c", "As at March 31, 2026 the current liabilities of the Company exceed its current assets.")
    assert _cells(res, "NARR-02") == ["A13"]


def test_narr02_trend_claim(tmp_path: Path) -> None:
    res = _run(tmp_path, "t", "Revenue from operations decreased compared with the previous year.")
    assert _cells(res, "NARR-02") == ["A13"]


def test_narr03_stale_figure(tmp_path: Path) -> None:
    res = _run(tmp_path, "s", "Cash and cash equivalents as at March 31, 2026 were ₹ 60.25 lakhs.")
    assert _cells(res, "NARR-03") == ["A13"]


def test_narr04_same_metric_two_values(tmp_path: Path) -> None:
    res = _run(tmp_path, "i", "The net worth of the Company as at March 31, 2026 stood at ₹ 517.95 lakhs.",
               "The net worth of the Company as at March 31, 2026 is ₹ 515.00 lakhs.")
    assert sorted(_cells(res, "NARR-04")) == ["A13", "A14"]
    assert _cells(res, "NARR-01") == ["A14"]


def test_narr05_unit_suspicion(tmp_path: Path) -> None:
    res = _run(tmp_path, "u", "Revenue from operations for the year ended March 31, 2026 was ₹ 1,25,000.00 lakhs.")
    assert _cells(res, "NARR-05") == ["A13"]


def test_txt06_text_vs_table_of_same_note(tmp_path: Path) -> None:
    res = _run(tmp_path, "x", "Income tax demands under appeal amount to ₹ 5.40 lakhs as at March 31, 2026.")
    assert _cells(res, "TXT-06") == ["A13"]


def test_unresolved_sentences_for_ai_handoff(tmp_path: Path) -> None:
    from fscore.narrative import unresolved_sentences

    path = build_entity(tmp_path / "ai.xlsx", EntityOpts(extra_narrative=["Capital commitments of ₹ 7.77 lakhs remain outstanding for office fit-outs."]))
    cfg = config()
    ctx = build_context(path, cfg)
    run_checks(path, cfg, context=ctx)
    out = unresolved_sentences(ctx)
    assert any("Capital commitments" in r["sentence"] for r in out)


def test_engine_is_offline(tmp_path: Path, no_network: None) -> None:
    res = _run(tmp_path, "offline", "Revenue from operations for the year ended March 31, 2026 amounted to ₹ 1,205.00 lakhs.")
    assert _cells(res, "NARR-01") == ["A13"]
