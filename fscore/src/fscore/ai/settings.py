"""Resolve provider settings from ``AIConfig`` + environment / OS keyring.

Secrets (API keys, gateway tokens) come only from environment variables or
the OS keyring (service ``fs-platform``) — never from configuration files,
payloads or logs. Region is mandatory for every external provider and has no
default (02 §9.3): it is shown in the approval dialog.

Environment variables read here:

* ``ANTHROPIC_API_KEY`` / keyring ``anthropic_api_key`` — Anthropic API (else the SDK's own
  credential chain, e.g. an ``ant auth login`` profile).
* ``ANTHROPIC_VERTEX_PROJECT_ID`` or ``FSCORE_AI_VERTEX_PROJECT`` — Google Vertex project.
* ``FSCORE_AI_FOUNDRY_RESOURCE`` (or ``AIConfig.endpoint``) — Microsoft Foundry resource name;
  ``ANTHROPIC_FOUNDRY_API_KEY`` / keyring ``foundry_api_key``.
* ``FSCORE_AI_GATEWAY_TOKEN`` / keyring ``gateway_token`` — firm gateway bearer token.
* ``FSCORE_AI_LOCAL_FLAVOUR`` (``openai`` | ``ollama``), ``FSCORE_AI_LOCAL_MODEL`` — local model.
* ``FSCORE_AI_REFUSAL_FALLBACK`` (``0`` disables the refusal fallback, default on).
"""

from __future__ import annotations

import os
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from fscore.ai.errors import AIConfigError
from fscore.config import AIConfig

KEYRING_SERVICE = "fs-platform"
EXTERNAL_PROVIDERS = ("anthropic", "bedrock", "vertex", "foundry", "gateway")
ANTHROPIC_GEOS = ("us", "global")  # Messages API inference_geo values

# fallback chains for platforms without server-side fallbacks (client-side middleware)
FALLBACK_MODEL = {"bedrock": "anthropic.claude-opus-4-8", "vertex": "claude-opus-4-8", "foundry": "claude-opus-4-8"}


class ProviderSettings(BaseModel):
    provider: str
    model: str
    region: str | None
    effort: str = "high"
    max_tokens: int = 16000
    endpoint: str | None = None
    vertex_project_id: str | None = None
    foundry_resource: str | None = None
    local_flavour: Literal["openai", "ollama"] = "ollama"
    local_model: str | None = None
    refusal_fallback: bool = True
    fallback_model: str | None = None
    timeout_s: float = 600.0
    max_retries: int = 2
    retain_payloads: str = "hash_only"
    extra: dict[str, str] = Field(default_factory=dict)

    @property
    def platform_model(self) -> str:
        """Model id as requested on the platform (Bedrock ids carry the ``anthropic.`` prefix)."""
        if self.provider == "bedrock" and not self.model.startswith("anthropic."):
            return "anthropic." + self.model
        return self.model

    @property
    def endpoint_host(self) -> str | None:
        if self.provider in ("local", "gateway") and self.endpoint:
            return urlparse(self.endpoint).hostname
        if self.provider == "foundry":
            return self.foundry_resource
        return None

    def fallback_description(self) -> str:
        if not self.refusal_fallback:
            return "off"
        if self.provider == "anthropic":
            return "server-side fallbacks=default (beta server-side-fallback-2026-07-01)"
        if self.provider in FALLBACK_MODEL:
            return f"client-side refusal fallback to {self.fallback_model or FALLBACK_MODEL[self.provider]}"
        return "off"


def is_localhost(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".localhost")


def resolve_settings(config: AIConfig, *, env: dict[str, str] | None = None) -> ProviderSettings:
    """Validate the AI configuration and fill platform details from the environment."""
    e = dict(os.environ) if env is None else env
    provider = config.provider
    region = (config.region or "").strip() or None
    if provider in EXTERNAL_PROVIDERS and region is None:
        raise AIConfigError(f"provider {provider!r} needs an explicit region (no default by design)")
    if provider == "anthropic" and region not in ANTHROPIC_GEOS:
        raise AIConfigError(
            f"region {region!r} is not an Anthropic API inference geography ({', '.join(ANTHROPIC_GEOS)}); "
            "for India-region processing use Amazon Bedrock (e.g. ap-south-1) or Vertex AI (asia-south1)"
        )
    s = ProviderSettings(
        provider=provider,
        model=config.model,
        region=region,
        effort=config.effort,
        max_tokens=config.max_tokens,
        endpoint=config.endpoint,
        retain_payloads=config.retain_payloads,
        refusal_fallback=e.get("FSCORE_AI_REFUSAL_FALLBACK", "1") not in ("0", "false", "no"),
    )
    if provider == "vertex":
        s.vertex_project_id = e.get("ANTHROPIC_VERTEX_PROJECT_ID") or e.get("FSCORE_AI_VERTEX_PROJECT")
        if not s.vertex_project_id:
            raise AIConfigError("Vertex AI needs a GCP project id (ANTHROPIC_VERTEX_PROJECT_ID)")
    if provider == "foundry":
        s.foundry_resource = config.endpoint or e.get("FSCORE_AI_FOUNDRY_RESOURCE")
        if not s.foundry_resource:
            raise AIConfigError("Microsoft Foundry needs a resource name (AIConfig.endpoint or FSCORE_AI_FOUNDRY_RESOURCE)")
    if provider in FALLBACK_MODEL:
        s.fallback_model = FALLBACK_MODEL[provider]
    if provider == "local":
        if not config.endpoint:
            raise AIConfigError("the local provider needs AIConfig.endpoint (e.g. http://127.0.0.1:11434)")
        if not is_localhost(config.endpoint):
            raise AIConfigError("the local provider only talks to localhost; use the gateway provider for remote endpoints")
        flavour = e.get("FSCORE_AI_LOCAL_FLAVOUR", "ollama")
        s.local_flavour = "openai" if flavour == "openai" else "ollama"
        s.local_model = e.get("FSCORE_AI_LOCAL_MODEL") or (config.model if not config.model.startswith("claude") else None)
        if not s.local_model:
            raise AIConfigError("the local provider needs a local model name (FSCORE_AI_LOCAL_MODEL); it never routes Claude calls")
        if s.local_model.lower().startswith("claude"):
            raise AIConfigError("the local provider must not route Claude calls")
        s.model = s.local_model
    if provider == "gateway":
        if not config.endpoint:
            raise AIConfigError("the gateway provider needs AIConfig.endpoint (the firm's AI gateway URL)")
        parsed = urlparse(config.endpoint)
        if parsed.scheme != "https" and not is_localhost(config.endpoint):
            raise AIConfigError("the firm gateway must be reached over HTTPS")
    return s


def get_secret(name: str, env_var: str | None = None, *, env: dict[str, str] | None = None) -> str | None:
    """Secret from the environment, else from the OS keyring; never from files or config."""
    e = dict(os.environ) if env is None else env
    if env_var and e.get(env_var):
        return e[env_var]
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, name)
    except Exception:  # no keyring backend on this machine
        return None
