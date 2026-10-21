"""Provider interface and the result record of one AI call.

A provider only transports: it receives the approved payload, the rendered
system prompt and the output schema, and returns the raw completion with its
stop reason and usage. Validation, de-pseudonymisation, number re-verification,
budgets and audit records are handled once, in :mod:`fscore.ai.review`.

Network-capable providers are imported lazily, so the default provider
``none`` never loads an HTTP or SDK module (02 §3.1 offline by default).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from fscore.ai.errors import AIConfigError
from fscore.ai.settings import ProviderSettings

Status = Literal[
    "ok", "refused", "max_tokens", "invalid_output", "error", "disabled", "budget_exceeded", "not_approved", "config_error"
]


class ProviderRequest(BaseModel):
    system: str
    user: str
    output_schema: dict[str, Any]
    output_model_name: str
    max_tokens: int
    effort: str
    payload_sha256: str


class RawCompletion(BaseModel):
    """Transport-level outcome. ``text`` is None unless the model finished normally."""

    text: str | None = None
    stop_reason: str | None = None
    stop_category: str | None = None  # refusal category when stop_reason == "refusal"
    request_id: str | None = None
    served_model: str | None = None
    fallback_ran: bool = False
    usage: dict[str, int] = Field(default_factory=dict)
    error: str | None = None  # exception class name only, never content


class CitationResult(BaseModel):
    owner: str
    ref: str
    cited: str
    actual: str | None = None
    status: str


class AIProposal(BaseModel):
    """One advisory item for a person to accept or reject (E-AI-05)."""

    kind: Literal["triage", "rewording", "xref", "explanation", "going_concern"]
    target: str  # finding id, sentence id or "a~b" pair
    content: dict[str, Any]
    verified: bool  # every cited number matched the engine's figures
    unverified: list[str] = Field(default_factory=list)  # refs that did not match or could not be found
    requires_user_action: bool = True


class AIResult(BaseModel):
    ok: bool
    status: Status
    purpose: str
    provider: str
    model: str
    region: str | None = None
    prompt_id: str = ""
    prompt_version: str = ""
    prompt_sha256: str = ""
    payload_sha256: str = ""
    request_id: str | None = None
    stop_reason: str | None = None
    stop_category: str | None = None
    served_model: str | None = None
    refusal_fallback: str = "off"
    fallback_ran: bool = False
    usage: dict[str, int] = Field(default_factory=dict)
    cost_usd: str = "0"
    output: dict[str, Any] | None = None  # validated, de-pseudonymised, unscaled
    proposals: list[AIProposal] = Field(default_factory=list)
    citations: list[CitationResult] = Field(default_factory=list)
    response_sha256: str | None = None
    retained_payload: str | None = None  # only when retain_payloads == "full"
    retained_response: str | None = None
    budget_alert: bool = False
    message: str = ""
    approval: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    duration_ms: int = 0

    def audit_record(self) -> dict[str, Any]:
        """Fields for audit_event.details (no client content unless retention is 'full')."""
        return self.model_dump(mode="json", exclude={"output", "proposals"})


class Provider(Protocol):
    name: str

    def send(self, request: ProviderRequest, settings: ProviderSettings) -> RawCompletion: ...


def get_provider(settings: ProviderSettings, **kwargs: Any) -> Provider:
    """Instantiate the configured provider (lazy imports; ``none`` loads nothing)."""
    name = settings.provider
    if name == "none":
        from fscore.ai.providers.none import NoneProvider

        return NoneProvider()
    if name == "mock":
        from fscore.ai.providers.mock import MockProvider

        return MockProvider(**kwargs)
    if name in ("anthropic", "bedrock", "vertex", "foundry"):
        from fscore.ai.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider(**kwargs)
    if name == "gateway":
        from fscore.ai.providers.gateway import GatewayProvider

        return GatewayProvider(**kwargs)
    if name == "local":
        from fscore.ai.providers.local import LocalProvider

        return LocalProvider(**kwargs)
    raise AIConfigError(f"unknown provider {name!r}")
