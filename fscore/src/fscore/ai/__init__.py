"""Optional AI reviewer behind a single, approval-gated egress (02 §9, C9, E-AI-01…05).

Offline by default: with provider ``none`` nothing is sent and no network or SDK
module is imported. This package is the only part of fscore allowed to open a
network connection, and only after a user other than the requester approved
the exact payload.

Facade used by the web platform (fsweb) and the CLI::

    preview = prepare_request(findings, purpose="triage", config=cfg.ai,
                              names={"entities": [...], "persons": [...], "parties": [...],
                                     "identifiers": {"din": [...]}},
                              context={"units": "₹ lakhs", "windows": [...], "metrics": [...],
                                       "paragraphs": [...], "figures": [...],
                                       "requested_by": "staff@firm", "engagement": "E-12",
                                       "reason": "triage before manager review"})
    # show preview.payload_json + preview.approval_summary(); a different user approves:
    token, record = approve_request(preview, approver="manager@firm", secret=SECRET)
    result = send_request(preview, token, config=cfg.ai, secret=SECRET)   # -> AIResult

``prepare_request(findings, *, purpose, config, names=None, context=None) -> AIRequestPreview``
    Builds the minimised, pseudonymised (and optionally amount-scaled) payload, its
    SHA-256, size, token and cost estimate, and the destination (provider, model,
    region, refusal-fallback setting). ``preview.local`` holds the token map, scale
    factor and original values; it is stored locally and never sent.

``send_request(preview, approval_token, *, config, secret, requester=None, provider=None,
               settings=None, budget=None, lookup=None, now=None) -> AIResult``
    Re-hashes the payload, verifies the approval (bound to the hash, unexpired, not
    self-approved), checks destination, model registry and budget, calls the provider,
    validates the structured output, restores names, unscales amounts and re-verifies
    every cited number. ``AIResult.proposals`` are advisory; ``AIResult.audit_record()``
    is ready for the audit trail.

Purposes: ``triage``, ``narrative``, ``xref``, ``explain`` and ``full_statements``
(the last only with ``context["allow_full_statements"] = True``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import timedelta
from functools import lru_cache
from importlib import resources
from typing import Any

from fscore.ai.approval import DEFAULT_TTL, issue_approval, verify_approval
from fscore.ai.budget import BudgetDecision, BudgetLedger, BudgetLimits
from fscore.ai.errors import (
    AIConfigError,
    AIDisabledError,
    AIError,
    ApprovalError,
    BudgetExceededError,
    PayloadError,
)
from fscore.ai.payload import (
    AIPayload,
    AIRequestPreview,
    Destination,
    build_preview,
    context_windows_from_workbook,
    split_sentences,
)
from fscore.ai.platform import build_payload, send_payload
from fscore.ai.provider import AIProposal, AIResult
from fscore.ai.redact import AmountScaler, NameCatalogue, Pseudonymiser
from fscore.ai.registry import MODEL_REGISTRY, list_prompts, prompt_for
from fscore.ai.review import send_request
from fscore.config import AIConfig
from fscore.models import Finding


def prepare_request(
    findings: Sequence[Finding],
    *,
    purpose: str,
    config: AIConfig,
    names: NameCatalogue | Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
) -> AIRequestPreview:
    """Build the payload preview a user approves (nothing is sent)."""
    return build_preview(findings, purpose=purpose, config=config, names=names, context=context)


def approve_request(
    preview: AIRequestPreview,
    *,
    approver: str,
    secret: str | bytes,
    ttl: timedelta = DEFAULT_TTL,
) -> tuple[str, dict[str, Any]]:
    """Issue an approval token bound to ``preview.sha256`` (approver ≠ requester)."""
    return issue_approval(preview.sha256, approver=approver, requester=preview.requested_by or "", secret=secret, ttl=ttl)


@lru_cache(maxsize=1)
def policy_text() -> str:
    """The AI policy (E-AI-01) shown in the admin console; users accept it once."""
    return resources.files("fscore.ai").joinpath("policy.md").read_text(encoding="utf-8")


def policy_version() -> str:
    for line in policy_text().splitlines():
        if line.lower().startswith("version:"):
            return line.split(":", 1)[1].strip()
    return "unknown"


__all__ = [
    "MODEL_REGISTRY",
    "AIConfigError",
    "AIDisabledError",
    "AIError",
    "AIPayload",
    "AIProposal",
    "AIRequestPreview",
    "AIResult",
    "AmountScaler",
    "ApprovalError",
    "BudgetDecision",
    "BudgetExceededError",
    "BudgetLedger",
    "BudgetLimits",
    "Destination",
    "NameCatalogue",
    "PayloadError",
    "Pseudonymiser",
    "approve_request",
    "build_payload",
    "context_windows_from_workbook",
    "issue_approval",
    "list_prompts",
    "policy_text",
    "policy_version",
    "prepare_request",
    "prompt_for",
    "send_payload",
    "send_request",
    "split_sentences",
    "verify_approval",
]
