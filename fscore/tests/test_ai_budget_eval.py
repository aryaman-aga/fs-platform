"""Budgets (E-AI-04), evaluation set (E-AI-02), prompt/model registry (E-AI-03), policy (E-AI-01)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from fscore.ai import (
    AIConfigError,
    BudgetLedger,
    BudgetLimits,
    approve_request,
    policy_text,
    policy_version,
    prepare_request,
    send_request,
)
from fscore.ai.eval import DATASET_VERSION, compare, dataset_sha256, narrative_cases, run_eval, triage_cases
from fscore.ai.providers.mock import MockProvider
from fscore.ai.registry import estimate_cost_usd, list_prompts, require_approved_model
from fscore.config import AIConfig
from fscore.models import Finding, Severity

NOW = datetime(2026, 10, 21, tzinfo=UTC)


def test_budget_alert_and_refusal(tmp_path: Path) -> None:
    store = tmp_path / "budget.json"
    led = BudgetLedger(BudgetLimits(firm_monthly_usd=Decimal("10"), engagement_monthly_usd=Decimal("2")), store=store)
    d = led.check("E-1", Decimal("1.50"), now=NOW)
    assert d.allowed
    assert not d.alert
    led.record("E-1", Decimal("1.50"), now=NOW)
    d2 = led.check("E-1", Decimal("0.20"), now=NOW)
    assert d2.allowed
    assert d2.alert
    d3 = led.check("E-1", Decimal("0.60"), now=NOW)
    assert not d3.allowed
    assert "engagement" in d3.reason
    assert BudgetLedger(led.limits, store=store).spent("eng:E-1", "2026-10") == Decimal("1.50")  # persisted
    assert led.check("E-1", Decimal("0.60"), now=datetime(2026, 11, 2, tzinfo=UTC)).allowed  # new month


def test_send_refused_when_budget_exhausted() -> None:
    cfg = AIConfig(provider="anthropic", region="global")
    f = Finding(id="F-1", rule_id="CAST-01", severity=Severity.HIGH, sheet="BS", cells=["C9"], printed="1.00")
    p = prepare_request([f], purpose="triage", config=cfg, context={"requested_by": "s", "engagement": "E-9"})
    token, _ = approve_request(p, approver="m", secret="k")
    led = BudgetLedger(BudgetLimits(engagement_monthly_usd=Decimal("0.0000001")))
    prov = MockProvider()
    res = send_request(p, token, config=cfg, secret="k", provider=prov, budget=led)
    assert res.status == "budget_exceeded"
    assert res.budget_alert
    assert prov.calls == []


def test_successful_call_is_recorded_against_budget() -> None:
    cfg = AIConfig(provider="mock")
    f = Finding(id="F-1", rule_id="CAST-01", severity=Severity.HIGH, sheet="BS", cells=["C9"], printed="1.00")
    p = prepare_request([f], purpose="triage", config=cfg, context={"requested_by": "s", "engagement": "E-2"})
    token, _ = approve_request(p, approver="m", secret="k")
    led = BudgetLedger(BudgetLimits(firm_monthly_usd=Decimal("5")))
    res = send_request(p, token, config=cfg, secret="k", provider=MockProvider(), budget=led)
    assert res.ok
    assert led.spent("firm") == Decimal(res.cost_usd)


def test_eval_set_is_large_labelled_and_reproducible() -> None:
    t, n = triage_cases(), narrative_cases()
    assert len(t) + len(n) >= 100
    assert {c.label for c in t} == {"genuine", "presentation_only", "false_positive"}
    assert {c.label for c in n} == {"yes", "no"}
    assert dataset_sha256() == dataset_sha256()
    # synthetic only: no realistic company identifiers
    blob = " ".join(c.sentence for c in n) + " ".join(c.finding.explanation for c in t)
    assert "Limited" not in blob
    assert "DIN:" not in blob


def test_eval_harness_on_mock_and_release_gate(no_network: None) -> None:
    report = run_eval()
    assert report.dataset_version == DATASET_VERSION
    assert report.total_items >= 100
    tri, nar = report.metrics["triage"], report.metrics["narrative"]
    assert tri.answered == tri.n
    assert nar.answered == nar.n
    assert tri.accuracy >= 0.85
    assert tri.precision >= 0.85
    assert tri.recall >= 0.85
    assert nar.precision >= 0.8
    assert nar.recall >= 0.8
    assert report.prompt_versions["triage"]
    worse = report.model_copy(deep=True)
    worse.metrics["triage"].recall -= 0.10
    assert compare(report, worse) == [f"triage: recall fell from {tri.recall:.3f} to {tri.recall - 0.10:.3f}"]
    assert compare(report, report) == []


def test_prompt_and_model_registry() -> None:
    prompts = {p.use_case: p for p in list_prompts()}
    assert set(prompts) == {"triage", "narrative", "xref", "explain"}
    for p in prompts.values():
        assert p.version
        assert len(p.sha256) == 64
        assert re.search(r"(?i)(do not|never) invent", p.body)
        assert "cited_numbers" in p.body
    require_approved_model("anthropic", "claude-opus-5-5")
    require_approved_model("bedrock", "anthropic.claude-opus-5-5")
    with pytest.raises(AIConfigError):
        require_approved_model("anthropic", "claude-unknown-9")
    assert estimate_cost_usd("anthropic", "claude-opus-5-5", 1_000_000, 0) == Decimal("4.000000")


def test_policy_is_published() -> None:
    text = policy_text()
    assert policy_version() == "1.0"
    for must in ("Allowed uses", "Prohibited uses", "Human in the loop", "Pseudonymisation", "approves"):
        assert must in text
