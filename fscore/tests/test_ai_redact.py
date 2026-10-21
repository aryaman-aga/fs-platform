"""Pseudonymisation and amount scaling for AI payloads (02 §9.1)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from fscore.ai.redact import AmountScaler, NameCatalogue, Pseudonymiser

NAMES = NameCatalogue(
    entities=["Corvus Data Services Private Limited"],
    parties=["Corvus Holdings Private Limited", "Corvus Technologies Inc."],
    persons=["Asha Raman", "Vikram Sethi", "Rohit Mehra"],
    identifiers={"din": ["01234567", "07654321"]},
)

TEXT = (
    "Corvus Data Services Private Limited (CIN U72900KA2015PTC081234) owes Corvus Holdings Private Limited "
    "₹ 38.19 lakhs. Signed by Asha Raman (DIN: 01234567) and Vikram Sethi (DIN 07654321); auditor Rohit Mehra, "
    "Membership No. 234567, FRN 123456W; PAN ABCPE1234F; GSTIN 29ABCPE1234F1Z5; asha@corvus.example.in."
)

SECRETS = [
    "Corvus Data Services", "Corvus Holdings", "Asha Raman", "Vikram Sethi", "Rohit Mehra", "01234567", "07654321",
    "U72900KA2015PTC081234", "234567", "123456W", "ABCPE1234F", "29ABCPE1234F1Z5", "asha@corvus.example.in",
]


def test_every_identifier_is_replaced_and_restored() -> None:
    p = Pseudonymiser(NAMES)
    red = p.redact_text(TEXT)
    for s in SECRETS:
        assert s not in red, s
    assert "ENTITY_1" in red
    assert "PARTY_1" in red
    assert "PERSON_1" in red
    assert "DIN_A" in red
    assert "DIN_B" in red
    assert "CIN_A" in red
    assert "PAN_A" in red
    assert "GSTIN_A" in red
    assert "MEMNO_A" in red
    assert "FRN_A" in red
    assert "EMAIL_A" in red
    assert "38.19" in red  # amounts are not identity data
    assert p.restore_text(red) == TEXT


def test_tokens_are_stable_across_calls_and_spellings() -> None:
    p = Pseudonymiser(NAMES)
    a = p.redact_text("Asha Raman approved.")
    b = p.redact_text("ASHA   RAMAN signed; asha raman again.")
    assert a.startswith("PERSON_1")
    assert b.count("PERSON_1") == 2
    # legal-suffix variants and the unambiguous core name map to the canonical party
    c = p.redact_text("Payable to Corvus Holdings Pvt. Ltd. and to Corvus Holdings.")
    assert c.count("PARTY_1") == 2
    assert p.restore_text("PARTY_1") == "Corvus Holdings Private Limited"


def test_map_export_import_continues_numbering() -> None:
    p1 = Pseudonymiser(NAMES)
    p1.redact_text("Asha Raman and Vikram Sethi")
    exported = p1.export_map()
    p2 = Pseudonymiser(NameCatalogue(persons=["Asha Raman", "New Person"]), mapping=exported)
    out = p2.redact_text("Asha Raman met New Person")
    assert "PERSON_1" in out  # same token as before
    next_no = sum(1 for k in exported if k.startswith("PERSON_")) + 1
    assert f"PERSON_{next_no}" in out  # numbering continues after the imported tokens


def test_dict_redaction_forces_tokens_for_identifier_keys() -> None:
    p = Pseudonymiser()
    out = p.redact({"name": "x", "din": "99999999", "nested": [{"pan": "AAAAA0000A"}], "amount": 12.5})
    assert out["din"] == "DIN_A"
    assert out["nested"][0]["pan"] == "PAN_A"
    assert out["amount"] == 12.5
    assert p.restore(out)["din"] == "99999999"


def test_aadhaar_like_numbers_are_masked() -> None:
    p = Pseudonymiser()
    assert "AADHAAR_A" in p.redact_text("Aadhaar 2345 6789 0123 on file")


def test_amount_scaling_is_exact_and_reversible() -> None:
    s = AmountScaler(Decimal("3.47"))
    text = "Loss ₹ 73.58 lakhs (PY (1,482.17)); net worth 12,34,567.89; 8.5% rate; year 2026; Note 12; dated 31.03.2026"
    scaled = s.scale_text(text)
    assert "73.58" not in scaled
    assert "1,482.17" not in scaled
    assert "8.5%" in scaled
    assert "2026" in scaled
    assert "Note 12" in scaled
    assert "31.03.2026" in scaled
    assert "42,83,950.5783" in scaled  # Indian grouping preserved
    assert s.unscale_text(scaled) == text


def test_scaling_preserves_equalities_and_ratios() -> None:
    s = AmountScaler(Decimal("2.17"))
    a, b, c = Decimal("100.10"), Decimal("200.25"), Decimal("300.35")
    sa, sb, sc = (Decimal(s.scale_number_text(str(x)).replace(",", "")) for x in (a, b, c))
    assert sa + sb == sc
    assert sa / sb == a / b
    assert s.unscale_number_text(f"{sa}") == "100.10"


def test_random_factor_is_never_one() -> None:
    import random

    for seed in range(50):
        f = AmountScaler(rng=random.Random(seed)).factor
        assert f != Decimal(1)
        assert Decimal("1.11") <= f <= Decimal("9.99")


def test_bad_factor_rejected() -> None:
    with pytest.raises(ValueError):
        AmountScaler(Decimal(0))
