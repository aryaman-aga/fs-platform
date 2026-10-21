"""fscore.ai.build_payload / send_payload: the bridge the web platform's AI gate uses (C9, E-AI-05)."""

from __future__ import annotations

import json

import pytest

from fscore.ai import build_payload, send_payload
from fscore.config import AIConfig

FINDINGS = [
    {"fid": "F-0001", "rule_id": "GC-01", "severity": "High", "certainty": "Likely", "sheet": "Note 29-34", "cells": ["A6"],
     "caption": "Going concern", "printed": "78.92", "expected": "78.89", "diff": "0.03",
     "explanation": "Corvus Data Services Private Limited: the note quotes 78.92 but Asha Raman (DIN 01234567) signed 78.89."},
]
NAMES = {"Corvus Data Services Private Limited": "ENTITY", "Asha Raman": "PERSON", "01234567": "DIN"}


def _built() -> dict:  # type: ignore[type-arg]
    return build_payload(kind="triage", findings=FINDINGS, context={"units": "₹ lakhs", "names": NAMES, "metrics": {"pat:CY": "78.89"}},
                         config=AIConfig(provider="mock"))


def test_payload_is_pseudonymised_and_mapping_stays_local() -> None:
    built = _built()
    sent = json.dumps(built["payload"])
    for secret in NAMES:
        assert secret not in sent
    assert "ENTITY_" in sent and "PERSON_" in sent
    assert "preview" in built["mapping"] and built["sha256"]


def test_send_restores_names_and_records_destination() -> None:
    built = _built()
    res = send_payload(payload=built["payload"], config=AIConfig(provider="mock"), mapping=built["mapping"], requester="staff@firm", approver="manager@firm")
    assert res["ok"] is True and res["stop_reason"]
    assert res["provider"] == "mock" and res["payload_sha256"] == built["sha256"]


def test_self_approval_and_tampering_refused() -> None:
    built = _built()
    with pytest.raises(ValueError, match="different user"):
        send_payload(payload=built["payload"], config=AIConfig(provider="mock"), mapping=built["mapping"], requester="staff@firm", approver="staff@firm")
    tampered = dict(built["payload"], units="₹ crores")
    with pytest.raises(ValueError, match="differs"):
        send_payload(payload=tampered, config=AIConfig(provider="mock"), mapping=built["mapping"], requester="staff@firm", approver="manager@firm")
