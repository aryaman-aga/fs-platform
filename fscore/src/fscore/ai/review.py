"""The AI review gate: approval → budget → provider → validation → restoration → re-verification.

``send_request`` is the only path to a model. It re-hashes the payload and
refuses when it differs from the approved hash or destination (C9), checks
the model against the registry (E-AI-03) and the budget (E-AI-04), checks the
stop reason before reading any content (refusal / max_tokens yield nothing),
validates the structured output (invalid output is discarded and logged by
hash only), maps tokens back to names locally, unscales amounts, and
re-verifies every number the model cites (E-AI-05). The result carries
proposals only; nothing here edits a workbook.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ValidationError

from fscore.ai.approval import verify_approval
from fscore.ai.budget import BudgetLedger
from fscore.ai.errors import AIConfigError, AIDisabledError, ApprovalError
from fscore.ai.payload import AIRequestPreview, scaling_note
from fscore.ai.provider import AIProposal, AIResult, CitationResult, Provider, ProviderRequest, get_provider
from fscore.ai.redact import AmountScaler, Pseudonymiser
from fscore.ai.registry import estimate_cost_usd, prompt_for, require_approved_model
from fscore.ai.schemas import output_model_for
from fscore.ai.settings import ProviderSettings, resolve_settings
from fscore.ai.verify import Lookup, collect_citations, verify_citations
from fscore.config import AIConfig

log = logging.getLogger("fscore.ai")

USER_TEMPLATE = "Review the payload below and answer in the required JSON format.\n\n<payload>\n{payload_json}\n</payload>"
SDK_PROVIDERS = ("anthropic", "bedrock", "vertex", "foundry", "gateway")


def output_schema(purpose: str, provider: str) -> dict[str, Any]:
    model = output_model_for(purpose)
    if provider in SDK_PROVIDERS:
        import anthropic

        return anthropic.transform_schema(model)
    return model.model_json_schema()


def render_system(preview: AIRequestPreview) -> str:
    prompt = prompt_for(preview.purpose)
    return prompt.render(units=preview.payload.units, scaling_note=scaling_note(preview.payload.amounts_scaled))


def _base(preview: AIRequestPreview) -> dict[str, Any]:
    return {
        "purpose": preview.purpose,
        "provider": preview.destination.provider,
        "model": preview.destination.model,
        "region": preview.destination.region,
        "prompt_id": preview.payload.prompt.id,
        "prompt_version": preview.payload.prompt.version,
        "prompt_sha256": preview.payload.prompt.sha256,
        "payload_sha256": preview.sha256,
        "refusal_fallback": preview.destination.refusal_fallback,
    }


def _restore(data: Any, preview: AIRequestPreview) -> Any:
    """Unscale amounts, then map tokens back to the firm's names (locally)."""
    if preview.local.scaler:
        scaler = AmountScaler.from_export(preview.local.scaler)

        def unscale(obj: Any, key: str | None = None) -> Any:
            if isinstance(obj, str):
                if key in ("value", "quoted_value"):
                    return scaler.unscale_number_text(obj)
                return scaler.unscale_text(obj)
            if isinstance(obj, dict):
                return {k: unscale(v, k) for k, v in obj.items()}
            if isinstance(obj, list):
                return [unscale(v, key) for v in obj]
            return obj

        data = unscale(data)
    if preview.local.pseudonyms:
        data = Pseudonymiser(mapping=preview.local.pseudonyms).restore(data)
    return data


def _proposals(purpose: str, output: BaseModel, checks: list[CitationResult]) -> list[AIProposal]:
    bad: dict[str, list[str]] = {}
    for c in checks:
        if c.status != "match":
            bad.setdefault(c.owner, []).append(c.ref)
    out: list[AIProposal] = []
    data = output.model_dump(mode="json")
    if purpose == "triage":
        for it in data["items"]:
            fid = it["finding_id"]
            out.append(AIProposal(kind="triage", target=fid, content={k: it[k] for k in ("classification", "reason", "suggested_wording")},
                                  verified=fid not in bad, unverified=bad.get(fid, [])))
    elif purpose in ("narrative", "full_statements"):
        for s in data["sentences"]:
            if s["consistent"] == "no" or s["suggested_rewording"]:
                sid = s["sentence_id"]
                out.append(AIProposal(kind="rewording", target=sid,
                                      content={k: s[k] for k in ("metric", "quoted_value", "period", "consistent", "reason", "suggested_rewording")},
                                      verified=sid not in bad, unverified=bad.get(sid, [])))
        if data.get("going_concern"):
            gc = data["going_concern"]
            out.append(AIProposal(kind="going_concern", target="going_concern",
                                  content={k: gc[k] for k in ("conclusion_supported", "observations", "disclosure_gaps")},
                                  verified="" not in bad, unverified=bad.get("", [])))
    elif purpose == "xref":
        for s in data["suggestions"]:
            pair = f"{s['a_ref']}~{s['b_ref']}"
            out.append(AIProposal(kind="xref", target=pair, content={k: s[k] for k in ("a_ref", "b_ref", "reason", "confidence")},
                                  verified=pair not in bad, unverified=bad.get(pair, [])))
    elif purpose == "explain":
        for it in data["items"]:
            fid = it["finding_id"]
            out.append(AIProposal(kind="explanation", target=fid, content={k: it[k] for k in ("explanation", "likely_cause")},
                                  verified=fid not in bad, unverified=bad.get(fid, [])))
    return out


def execute(
    preview: AIRequestPreview,
    provider: Provider,
    settings: ProviderSettings,
    *,
    lookup: Lookup | None = None,
    started_at: datetime | None = None,
) -> AIResult:
    """Provider call + output handling (used after approval, and by the evaluation harness)."""
    t0 = time.perf_counter()
    base = _base(preview)
    model_cls = output_model_for(preview.purpose)
    req = ProviderRequest(
        system=render_system(preview),
        user=USER_TEMPLATE.format(payload_json=preview.payload_json),
        output_schema=output_schema(preview.purpose, settings.provider),
        output_model_name=model_cls.__name__,
        max_tokens=settings.max_tokens,
        effort=settings.effort,
        payload_sha256=preview.sha256,
    )

    def done(**kw: Any) -> AIResult:
        res = AIResult(**{**base, **kw}, started_at=started_at or datetime.now(UTC), duration_ms=int((time.perf_counter() - t0) * 1000))
        log.info("ai call purpose=%s provider=%s status=%s payload=%s", res.purpose, res.provider, res.status, res.payload_sha256[:12])
        return res

    try:
        raw = provider.send(req, settings)
    except AIDisabledError as exc:
        return done(ok=False, status="disabled", message=str(exc))
    except AIConfigError as exc:
        return done(ok=False, status="config_error", message=str(exc))
    cost = estimate_cost_usd(settings.provider, settings.platform_model, raw.usage.get("input_tokens", 0), raw.usage.get("output_tokens", 0))
    common = {
        "request_id": raw.request_id,
        "stop_reason": raw.stop_reason,
        "stop_category": raw.stop_category,
        "served_model": raw.served_model,
        "fallback_ran": raw.fallback_ran,
        "usage": raw.usage,
        "cost_usd": str(cost),
    }
    if raw.error:
        return done(ok=False, status="error", message=raw.error, **common)
    if raw.stop_reason == "refusal":
        return done(ok=False, status="refused", message=f"the model declined (category: {raw.stop_category or 'unspecified'}); no content used", **common)
    if raw.stop_reason == "max_tokens":
        return done(ok=False, status="max_tokens", message="the response hit max_tokens; truncated output discarded", **common)
    if raw.text is None:
        return done(ok=False, status="invalid_output", message="empty response", **common)
    response_sha = hashlib.sha256(raw.text.encode("utf-8")).hexdigest()
    try:
        parsed = model_cls.model_validate_json(raw.text)
    except ValidationError as exc:
        log.warning("ai output rejected payload=%s response=%s errors=%d", preview.sha256[:12], response_sha[:12], exc.error_count())
        return done(ok=False, status="invalid_output", message="structured output failed validation and was discarded", response_sha256=response_sha, **common)
    restored = model_cls.model_validate(_restore(parsed.model_dump(mode="json"), preview))
    lk = lookup if lookup is not None else preview.local.lookup
    checks: list[CitationResult] = []
    for owner, cn in collect_citations(restored):
        for chk in verify_citations([cn], lk):
            checks.append(CitationResult(owner=owner, ref=chk.ref, cited=chk.cited, actual=chk.actual, status=chk.status))
    full = settings.retain_payloads == "full"
    return done(
        ok=True,
        status="ok",
        output=restored.model_dump(mode="json"),
        proposals=_proposals(preview.purpose, restored, checks),
        citations=checks,
        response_sha256=response_sha,
        retained_payload=preview.payload_json if full else None,
        retained_response=raw.text if full else None,
        **common,
    )


def send_request(
    preview: AIRequestPreview,
    approval_token: str,
    *,
    config: AIConfig,
    secret: str | bytes,
    requester: str | None = None,
    provider: Provider | None = None,
    settings: ProviderSettings | None = None,
    budget: BudgetLedger | None = None,
    lookup: Lookup | None = None,
    now: datetime | None = None,
) -> AIResult:
    started = datetime.now(UTC)
    base = _base(preview)
    if config.provider == "none":
        return AIResult(ok=False, status="disabled", message="AI review is disabled (provider 'none')", started_at=started, **base)
    canonical = preview.payload.canonical_json()
    actual_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if canonical != preview.payload_json or actual_hash != preview.sha256:
        return AIResult(ok=False, status="not_approved", message="payload changed after it was previewed", started_at=started, **base)
    try:
        approval = verify_approval(approval_token, payload_sha256=actual_hash, secret=secret, requester=requester or preview.requested_by, now=now)
    except ApprovalError as exc:
        return AIResult(ok=False, status="not_approved", message=str(exc), approval=exc.record, started_at=started, **base)
    try:
        s = settings or resolve_settings(config)
        require_approved_model(s.provider, s.platform_model)
    except AIConfigError as exc:
        return AIResult(ok=False, status="config_error", message=str(exc), approval=approval, started_at=started, **base)
    dest = preview.destination
    if (s.provider, s.platform_model, s.region) != (dest.provider, dest.model, dest.region):
        return AIResult(ok=False, status="not_approved", message="destination differs from the one approved", approval=approval, started_at=started, **base)
    alert = False
    if budget is not None:
        decision = budget.check(preview.engagement, Decimal(preview.cost_estimate_usd), now=now)
        alert = decision.alert
        if not decision.allowed:
            return AIResult(ok=False, status="budget_exceeded", message=decision.reason, approval=approval, budget_alert=True, started_at=started, **base)
    prov = provider or get_provider(s)
    result = execute(preview, prov, s, lookup=lookup, started_at=started)
    result.approval = approval
    if budget is not None:
        budget.record(preview.engagement, Decimal(result.cost_usd), now=now)
        after = budget.check(preview.engagement, Decimal(0), now=now)
        result.budget_alert = alert or after.alert
    return result
