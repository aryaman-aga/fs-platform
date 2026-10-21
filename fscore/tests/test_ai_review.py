"""Review orchestration: restoration, unscaling and re-verification of cited numbers (E-AI-05)."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from fscore.ai import approve_request, prepare_request, send_request
from fscore.ai.provider import ProviderRequest, RawCompletion
from fscore.ai.providers.mock import MockProvider
from fscore.ai.settings import ProviderSettings
from fscore.config import AIConfig
from fscore.models import Certainty, Finding, Severity

SECRET = "review-secret"
METRICS = {"pat:CY": "(64.27)", "pat:PY": "(51.90)", "net_worth:CY": "318.44", "net_worth:PY": "302.15", "cash:CY": "45.62"}
SENTENCES = [
    {"id": "S1", "text": "Corvus Data Services Private Limited has incurred a loss of ₹ 64.27 lakhs during the year ended March 31, 2026."},
    {"id": "S2", "text": "The net worth of the Company as at March 31, 2026 is ₹ 302.15 lakhs."},
    {"id": "S3", "text": "The loss for the quarter ended June 30, 2026 was ₹ 64.27 lakhs."},
    {"id": "S4", "text": "These financial statements have been prepared on a going concern basis."},
]
NAMES = {"entities": ["Corvus Data Services Private Limited"]}


def _run(cfg: AIConfig, provider: Any, purpose: str = "narrative", **ctx: Any) -> Any:
    context = {"paragraphs": SENTENCES, "metrics": METRICS, "requested_by": "staff@firm", **ctx}
    p = prepare_request([], purpose=purpose, config=cfg, names=NAMES, context=context)
    token, _ = approve_request(p, approver="manager@firm", secret=SECRET)
    return p, send_request(p, token, config=cfg, secret=SECRET, provider=provider)


def test_narrative_review_proposals_and_citations(no_network: None) -> None:
    p, res = _run(AIConfig(provider="mock"), MockProvider())
    assert "Corvus" not in p.payload_json
    assert res.ok, res.message
    by_id = {s["sentence_id"]: s for s in res.output["sentences"]}
    assert by_id["S1"]["consistent"] == "yes"
    assert by_id["S2"]["consistent"] == "no"  # stale previous-year figure quoted for the current year
    assert by_id["S3"]["consistent"] == "no"  # out-of-scope period
    targets = {pr.target for pr in res.proposals if pr.kind == "rewording"}
    assert targets == {"S2", "S3"}
    assert all(c.status == "match" for c in res.citations)
    assert all(pr.verified and pr.requires_user_action for pr in res.proposals)
    gc = [pr for pr in res.proposals if pr.kind == "going_concern"]
    assert gc
    assert gc[0].content["conclusion_supported"] == "unclear"


def test_wrong_cited_number_is_flagged(no_network: None) -> None:
    f = Finding(id="F-1", rule_id="CAST-01", severity=Severity.HIGH, certainty=Certainty.CERTAIN, sheet="BS", cells=["C20"],
                caption="Total assets", printed="9,876.54", explanation="Total does not cast.")
    cfg = AIConfig(provider="mock")
    p = prepare_request([f], purpose="triage", config=cfg, context={"requested_by": "s"})
    token, _ = approve_request(p, approver="m", secret=SECRET)
    res = send_request(p, token, config=cfg, secret=SECRET, provider=MockProvider(mode="wrong_number"))
    assert res.ok
    assert res.citations[0].status == "mismatch"
    assert res.citations[0].actual == "9,876.54"
    assert res.proposals[0].verified is False
    assert res.proposals[0].unverified == ["BS!C20"]


def test_names_and_amounts_restored_locally(no_network: None) -> None:
    class Echo:
        """Returns a rewording that uses the token and the scaled figure from the payload."""

        name = "echo"

        def send(self, request: ProviderRequest, settings: ProviderSettings) -> RawCompletion:
            payload = json.loads(request.user.split("<payload>")[1].split("</payload>")[0])
            text = payload["paragraphs"][0]["text"]
            scaled_loss = next(m["value"] for m in payload["metrics"] if m["key"] == "pat" and m["period"] == "CY")
            out = {"sentences": [{"sentence_id": "S1", "metric": "pat", "quoted_value": scaled_loss, "period": "CY", "consistent": "no",
                                  "reason": "check", "suggested_rewording": text, "cited_numbers": [{"ref": "pat:CY", "value": scaled_loss}]}],
                   "going_concern": None}
            return RawCompletion(text=json.dumps(out), stop_reason="end_turn", usage={"input_tokens": 10, "output_tokens": 10})

    cfg = AIConfig(provider="mock", scale_amounts=True)
    p, res = _run(cfg, Echo(), scale_factor=Decimal("2.50"))
    assert p.payload.amounts_scaled
    assert "64.27" not in p.payload_json
    assert "160.6750" in p.payload_json
    assert "ENTITY_1" in p.payload_json
    s = res.output["sentences"][0]
    assert s["quoted_value"] == "(64.27)"
    assert s["suggested_rewording"] == SENTENCES[0]["text"]  # name and amount both restored
    assert res.citations[0].status == "match"


def test_retention_policy(no_network: None) -> None:
    _p, hashed = _run(AIConfig(provider="mock"), MockProvider())
    assert hashed.retained_payload is None
    assert hashed.retained_response is None
    assert hashed.response_sha256
    _p2, full = _run(AIConfig(provider="mock", retain_payloads="full"), MockProvider())
    assert full.retained_payload
    assert full.retained_response
    assert "output" not in full.audit_record()


def test_xref_and_explain_purposes(no_network: None) -> None:
    cfg = AIConfig(provider="mock")
    figs = [{"ref": "BS!C12", "caption": "Trade receivables", "printed": "1,204.00", "period": "CY"},
            {"ref": "Note 6-11!D30", "caption": "Total trade receivables", "printed": "1,204.00", "period": "CY"}]
    p = prepare_request([], purpose="xref", config=cfg, context={"figures": figs, "requested_by": "s"})
    token, _ = approve_request(p, approver="m", secret=SECRET)
    res = send_request(p, token, config=cfg, secret=SECRET, provider=MockProvider())
    assert res.ok
    assert res.proposals[0].kind == "xref"
    assert res.proposals[0].verified
    f = Finding(id="F-9", rule_id="BAL-01", severity=Severity.HIGH, sheet="BS", cells=["C40"], printed="9,999.00", explanation="BS does not tally.")
    p2 = prepare_request([f], purpose="explain", config=cfg, context={"requested_by": "s"})
    t2, _ = approve_request(p2, approver="m", secret=SECRET)
    res2 = send_request(p2, t2, config=cfg, secret=SECRET, provider=MockProvider())
    assert res2.ok
    assert res2.proposals[0].kind == "explanation"


def test_mock_failure_modes_map_to_statuses(no_network: None) -> None:
    for mode, status in (("refusal", "refused"), ("max_tokens", "max_tokens"), ("invalid_json", "invalid_output"), ("error", "error")):
        _p, res = _run(AIConfig(provider="mock"), MockProvider(mode=mode))
        assert res.status == status
        assert res.output is None
        assert res.proposals == []
