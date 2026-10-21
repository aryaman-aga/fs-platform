"""Unit tests: number words, mention extraction, periods, claims and typed-text rewrites (02 §6.8)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from fscore.config import EngagementConfig
from fscore.narrative.extract import claims, extract_mentions, period_refs, split_sentences
from fscore.narrative.numwords import find_number_words, words_to_number
from fscore.narrative.rewrite import (
    Replacement,
    apply_replacements,
    format_amount,
    format_date_like,
    swap_polarity,
)
from fscore.narrative.terms import default_terms, find_phrases

LAKHS = EngagementConfig(units="lakhs")
MILLIONS_SPFS = EngagementConfig(units="millions", period_type="spfs")


# ---------------------------------------------------------------------------- number words


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("twelve lakh thirty-four thousand five hundred and sixty", 1_234_560),
        ("one crore twenty lakh", 12_000_000),
        ("five hundred and sixty", 560),
        ("two million three hundred thousand", 2_300_000),
        ("ninety-nine", 99),
    ],
)
def test_number_words(text: str, value: int) -> None:
    found = find_number_words(f"Rupees {text} only")
    assert len(found) == 1
    assert found[0].value == Decimal(value)


def test_lone_scale_word_is_not_a_number() -> None:
    assert find_number_words("₹ 4.71 million") == []
    assert words_to_number(["and"]) is None


# ---------------------------------------------------------------------------- sentences


def test_sentence_split_keeps_decimals_and_abbreviations() -> None:
    sents = split_sentences("Loss was Rs. 4.71 lakhs. Pvt. Ltd. companies are listed; the next one follows.")
    assert [s.text for s in sents] == ["Loss was Rs. 4.71 lakhs.", "Pvt. Ltd. companies are listed;", "the next one follows."]


# ---------------------------------------------------------------------------- amounts


def test_amount_units_normalised_to_statement_unit() -> None:
    ms = extract_mentions("Revenue for the year ended March 31, 2026 was ₹ 1.23 crores.", LAKHS)
    assert len(ms) == 1
    assert ms[0].value == Decimal("123.00")
    assert ms[0].unit == "crores"
    assert ms[0].metric == "revenue"
    assert ms[0].period == "CY"


def test_rupee_amount_with_indian_grouping() -> None:
    ms = extract_mentions("A demand of Rs. 1,23,456 is pending.", LAKHS)
    assert ms[0].unit == "rupees"
    assert ms[0].value == Decimal("1.23456")


def test_loss_wording_sets_negative_sign_and_previous_year_parenthetical() -> None:
    text = "The Company incurred a loss of ₹ 6.40 lakhs for the year ended March 31, 2026 (previous year: profit of ₹ 3.10 lakhs)."
    ms = [m for m in extract_mentions(text, LAKHS) if m.kind == "amount"]
    assert [(m.value, m.period, m.metric) for m in ms] == [(Decimal("-6.40"), "CY", "pat"), (Decimal("3.10"), "PY", "pat")]


def test_decimal_comma_is_flagged_malformed() -> None:
    ms = extract_mentions("The profit for the year ended March 31, 2026 was ₹ 23,58 million.", MILLIONS_SPFS)
    assert ms[0].malformed
    assert ms[0].value == Decimal("23.58")


def test_percentage_range_and_counts() -> None:
    ms = extract_mentions("Discount rates of 6.5% to 7.25% were used for 10,500,000 equity shares.", LAKHS)
    assert [m.kind for m in ms] == ["percent", "percent"]
    assert [m.raw for m in ms] == [Decimal("6.5"), Decimal("7.25")]


def test_number_words_amount() -> None:
    ms = extract_mentions("An advance of rupees twelve lakh thirty-four thousand was received.", LAKHS)
    assert ms[0].words
    assert ms[0].value == Decimal("12.34")


def test_quarter_and_year_respectively() -> None:
    text = "The Company incurred losses of ₹ 4.10 lakhs and ₹ 15.20 lakhs for the quarter and year ended March 31, 2026 respectively."
    ms = extract_mentions(text, EngagementConfig(units="lakhs", period_type="spfs"))
    assert [(m.value, m.period) for m in ms] == [(Decimal("-4.10"), "Q4_CY"), (Decimal("-15.20"), "CY")]


def test_out_of_scope_and_malformed_periods() -> None:
    refs = period_refs("Working capital as at June 30, 2026 and cash as at 31st June 2026.", LAKHS)
    assert [r.key for r in refs] == [None, None]
    assert refs[0].end_date == date(2026, 6, 30)
    assert refs[1].malformed


def test_metric_classification() -> None:
    text = ("Its current liabilities exceeded its current assets by ₹ 7.15 lakhs, the net worth was ₹ 312.40 lakhs, "
            "the holding company infused equity of ₹ 40.00 lakhs and cash and cash equivalents were ₹ 3.33 lakhs.")
    ms = extract_mentions(text, LAKHS)
    assert [m.metric for m in ms] == ["nca_ncl_gap", "net_worth", "equity_infused", "cash"]


def test_phrase_dictionary_prefers_specific_metric() -> None:
    hits = find_phrases("total comprehensive income for the year", default_terms())
    assert hits[0].metric == "tci"


# ---------------------------------------------------------------------------- claims


def test_claims() -> None:
    names = [c[0] for c in claims("The Company has a positive net worth; its current liabilities of the Company exceed its current assets "
                                    "and no material uncertainty exists. It earned a profit of ₹ 1.00 lakh.")]
    assert {"positive_net_worth", "cl_exceed_ca", "no_material_uncertainty", "earned_profit"} <= set(names)


# ---------------------------------------------------------------------------- rewrite


def test_format_amount_keeps_grouping_style() -> None:
    assert format_amount(Decimal("123456.7"), style_of="1,23,456.70") == "1,23,456.70"
    assert format_amount(Decimal("123456.7"), style_of="123,456.70") == "123,456.70"
    assert format_amount(Decimal("-4.5"), brackets=True) == "(4.50)"


@pytest.mark.parametrize(
    ("original", "expected"),
    [("31st June 2026", "31st March 2026"), ("June 30, 2026", "March 31, 2026"), ("30.06.2026", "31.03.2026"), ("30-Jun-2026", "31-Mar-2026")],
)
def test_format_date_like(original: str, expected: str) -> None:
    assert format_date_like(original, date(2026, 3, 31)) == expected


def test_swap_polarity_fixes_verb_and_noun() -> None:
    text = "The Company earned a profit of ₹ 3.00 lakhs."
    start = text.index("₹")
    out = apply_replacements(text, swap_polarity(text, start, start + 12, to="loss"))
    assert out == "The Company incurred a loss of ₹ 3.00 lakhs."


def test_apply_replacements_ignores_overlaps() -> None:
    out = apply_replacements("abc def", [Replacement(0, 3, "xyz", ""), Replacement(1, 2, "q", ""), Replacement(4, 7, "uvw", "")])
    assert out == "xyz uvw"
