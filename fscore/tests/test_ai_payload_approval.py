"""Payload minimisation, hashing and approval binding (C9, 02 §9.1)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import xlsxwriter

from fscore.ai import (
    AIConfigError,
    ApprovalError,
    PayloadError,
    approve_request,
    context_windows_from_workbook,
    issue_approval,
    prepare_request,
    send_request,
    verify_approval,
)
from fscore.ai.providers.mock import MockProvider
from fscore.config import AIConfig
from fscore.io.xlsx_reader import Workbook
from fscore.models import CellRef, Certainty, Evidence, Finding, Severity

SECRET = "test-secret-not-for-production"
NAMES = {
    "entities": ["Corvus Data Services Private Limited"],
    "persons": ["Asha Raman", "Vikram Sethi"],
    "parties": ["Corvus Holdings Private Limited"],
    "identifiers": {"din": ["01234567"]},
}


def finding(i: int = 1, **kw: object) -> Finding:
    base = {
        "id": f"F-{i:04d}",
        "rule_id": "CAST-01",
        "severity": Severity.HIGH,
        "certainty": Certainty.CERTAIN,
        "sheet": "Note 14-19",
        "cells": ["D25"],
        "caption": "Total trade payables",
        "printed": "1,234.56",
        "expected": "1,234.57",
        "difference": Decimal("-0.01"),
        "explanation": "Printed total does not cast; payable to Corvus Holdings Private Limited approved by Asha Raman (DIN 01234567).",
        "evidence": [Evidence(label="Payable to holding company", ref=CellRef(sheet="Note 14-19", addr="D22"), printed="38.19")],
        "key": "should-not-be-sent",
        "tags": ["internal"],
    }
    base.update(kw)
    return Finding(**base)  # type: ignore[arg-type]


def mock_cfg(**kw: object) -> AIConfig:
    return AIConfig(provider="mock", **kw)  # type: ignore[arg-type]


def test_payload_is_minimal_pseudonymised_and_hashed() -> None:
    p = prepare_request([finding()], purpose="triage", config=mock_cfg(), names=NAMES,
                        context={"units": "₹ lakhs", "requested_by": "staff@firm", "engagement": "E-1", "reason": "triage"})
    js = p.payload_json
    for secret in ("Corvus Holdings", "Asha Raman", "01234567", "Corvus Data Services"):
        assert secret not in js
    assert "should-not-be-sent" not in js
    assert "internal" not in js
    assert "fingerprint" not in js
    assert p.sha256 == hashlib.sha256(js.encode()).hexdigest() == p.payload.sha256
    assert p.size_bytes == len(js.encode())
    assert p.token_estimate > 0
    assert p.local.pseudonyms["PARTY_1"] == "Corvus Holdings Private Limited"
    assert p.local.lookup["note 14-19!d25"] == "1,234.56"
    summary = p.approval_summary()
    assert summary["payload_sha256"] == p.sha256
    assert summary["destination"]["provider"] == "mock"


def test_full_statements_need_explicit_flag_and_narrative_needs_text() -> None:
    with pytest.raises(PayloadError):
        prepare_request([], purpose="full_statements", config=mock_cfg(), context={"paragraphs": ["x"]})
    with pytest.raises(PayloadError):
        prepare_request([], purpose="narrative", config=mock_cfg())
    with pytest.raises(PayloadError):
        prepare_request([], purpose="everything", config=mock_cfg())
    p = prepare_request([], purpose="full_statements", config=mock_cfg(),
                        context={"allow_full_statements": True, "statements": [{"sheet": "BS", "rows": [{"row": 5, "caption": "Total assets", "values": {"C": "10.00"}}]}]})
    assert p.payload.statements[0].sheet == "BS"


def test_region_is_mandatory_for_external_providers() -> None:
    with pytest.raises(AIConfigError):
        prepare_request([finding()], purpose="triage", config=AIConfig(provider="anthropic"))
    with pytest.raises(AIConfigError):
        prepare_request([finding()], purpose="triage", config=AIConfig(provider="anthropic", region="ap-south-1"))
    p = prepare_request([finding()], purpose="triage", config=AIConfig(provider="bedrock", region="ap-south-1"))
    assert p.destination.model == "anthropic.claude-opus-5-5"
    assert p.destination.region == "ap-south-1"
    assert "client-side refusal fallback" in p.destination.refusal_fallback
    p2 = prepare_request([finding()], purpose="triage", config=AIConfig(provider="anthropic", region="global"))
    assert "fallbacks=default" in p2.destination.refusal_fallback
    assert Decimal(p2.cost_estimate_usd) > 0


def test_approval_binds_hash_and_rejects_changes() -> None:
    p = prepare_request([finding()], purpose="triage", config=mock_cfg(), names=NAMES, context={"requested_by": "staff@firm"})
    token, rec = approve_request(p, approver="manager@firm", secret=SECRET)
    assert rec["event"] == "ai.approval.issued"
    ok = verify_approval(token, payload_sha256=p.sha256, secret=SECRET, requester="staff@firm")
    assert ok["event"] == "ai.approval.verified"
    assert ok["approver"] == "manager@firm"
    other = hashlib.sha256(b"changed").hexdigest()
    with pytest.raises(ApprovalError) as e:
        verify_approval(token, payload_sha256=other, secret=SECRET)
    assert e.value.record["reason"] == "payload changed after approval"
    with pytest.raises(ApprovalError):
        verify_approval(token, payload_sha256=p.sha256, secret="another-secret")
    with pytest.raises(ApprovalError):
        verify_approval(token[:-2] + "00", payload_sha256=p.sha256, secret=SECRET)
    with pytest.raises(ApprovalError):
        verify_approval(token, payload_sha256=p.sha256, secret=SECRET, requester="someone-else@firm")


def test_self_approval_and_expiry_refused() -> None:
    h = hashlib.sha256(b"x").hexdigest()
    with pytest.raises(ApprovalError) as e:
        issue_approval(h, approver="staff@firm", requester="Staff@Firm", secret=SECRET)
    assert e.value.record["reason"] == "self-approval"
    t0 = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
    token, _ = issue_approval(h, approver="mgr@firm", requester="staff@firm", secret=SECRET, ttl=timedelta(minutes=5), now=t0)
    verify_approval(token, payload_sha256=h, secret=SECRET, now=t0 + timedelta(minutes=4))
    with pytest.raises(ApprovalError) as e2:
        verify_approval(token, payload_sha256=h, secret=SECRET, now=t0 + timedelta(minutes=6))
    assert e2.value.record["reason"] == "approval expired"


def test_send_request_end_to_end_with_mock(no_network: None) -> None:
    cfg = mock_cfg()
    p = prepare_request([finding(1), finding(2, rule_id="TXT-07", severity=Severity.LOW)], purpose="triage", config=cfg,
                        names=NAMES, context={"requested_by": "staff@firm"})
    token, _ = approve_request(p, approver="manager@firm", secret=SECRET)
    prov = MockProvider()
    res = send_request(p, token, config=cfg, secret=SECRET, provider=prov)
    assert res.ok, res.message
    assert res.status == "ok"
    assert len(prov.calls) == 1
    assert {pr.target for pr in res.proposals} == {"F-0001", "F-0002"}
    cls = {pr.target: pr.content["classification"] for pr in res.proposals}
    assert cls == {"F-0001": "genuine", "F-0002": "presentation_only"}
    assert all(c.status == "match" for c in res.citations)
    assert res.approval["event"] == "ai.approval.verified"
    rec = res.audit_record()
    assert "output" not in rec
    assert rec["payload_sha256"] == p.sha256
    assert rec["prompt_version"]


def test_send_request_refuses_tampering_and_missing_approval() -> None:
    cfg = mock_cfg()
    p = prepare_request([finding()], purpose="triage", config=cfg, context={"requested_by": "staff@firm"})
    token, _ = approve_request(p, approver="manager@firm", secret=SECRET)
    prov = MockProvider()
    assert send_request(p, "", config=cfg, secret=SECRET, provider=prov).status == "not_approved"
    tampered = p.model_copy(deep=True)
    tampered.payload.findings[0].printed = "9,999.99"
    r = send_request(tampered, token, config=cfg, secret=SECRET, provider=prov)
    assert r.status == "not_approved"
    assert "changed" in r.message
    # a re-built payload with a new hash is not covered by the old token
    tampered.payload_json = tampered.payload.canonical_json()
    tampered.sha256 = tampered.payload.sha256
    r2 = send_request(tampered, token, config=cfg, secret=SECRET, provider=prov)
    assert r2.status == "not_approved"
    assert prov.calls == []


def test_destination_change_after_approval_is_refused() -> None:
    p = prepare_request([finding()], purpose="triage", config=AIConfig(provider="bedrock", region="ap-south-1"), context={"requested_by": "s@f"})
    token, _ = approve_request(p, approver="m@f", secret=SECRET)
    prov = MockProvider()
    r = send_request(p, token, config=AIConfig(provider="bedrock", region="us-east-1"), secret=SECRET, provider=prov)
    assert r.status == "not_approved"
    assert "destination" in r.message
    assert prov.calls == []


def test_context_windows_from_workbook(tmp_path: Path) -> None:
    path = tmp_path / "ctx.xlsx"
    wb = xlsxwriter.Workbook(str(path))
    ws = wb.add_worksheet("Note 14-19")
    acc = wb.add_format({"num_format": '_(* #,##0.00_);_(* \\(#,##0.00\\);_(* "-"??_);_(@_)'})
    for r in range(20, 30):
        ws.write_string(r - 1, 0, f"Line {r}")
        ws.write_number(r - 1, 2, 100 + r + 0.5, acc)
        ws.write_number(r - 1, 3, -(50 + r), acc)
    wb.close()
    with Workbook.open(path) as book:
        windows = context_windows_from_workbook(book, [finding()], neighbours=2)
    w = windows[0]
    assert w.sheet == "Note 14-19"
    assert [r.row for r in w.rows] == [23, 24, 25, 26, 27]
    assert w.rows[2].caption == "Line 25"
    assert w.rows[2].values == {"C": "125.50", "D": "(75.00)"}
    p = prepare_request([finding()], purpose="explain", config=mock_cfg(), context={"windows": windows})
    assert p.local.lookup["note 14-19!d25"] == "(75.00)"
