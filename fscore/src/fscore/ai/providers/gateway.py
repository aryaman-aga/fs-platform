"""The firm's own AI gateway (an internal proxy with SSO) as a provider plugin (02 §9.1).

The gateway exposes the Anthropic Messages API, so the official SDK is used
with ``base_url`` set to the gateway and the SSO bearer token from the
environment / OS keyring (``FSCORE_AI_GATEWAY_TOKEN`` / keyring ``gateway_token``).
HTTPS is required (localhost excepted, for testing). Server-side fallbacks and
inference geography are left to the gateway's own policy.
"""

from __future__ import annotations

from typing import Any

from fscore.ai.errors import AIConfigError
from fscore.ai.providers.anthropic_provider import AnthropicProvider, ClientFactory
from fscore.ai.settings import ProviderSettings, get_secret


def build_gateway_client(settings: ProviderSettings) -> Any:
    import anthropic

    if not settings.endpoint:
        raise AIConfigError("gateway endpoint missing")
    token = get_secret("gateway_token", "FSCORE_AI_GATEWAY_TOKEN")
    if not token:
        raise AIConfigError("no gateway token in FSCORE_AI_GATEWAY_TOKEN or the OS keyring")
    return anthropic.Anthropic(base_url=settings.endpoint, auth_token=token, timeout=settings.timeout_s, max_retries=settings.max_retries)


class GatewayProvider(AnthropicProvider):
    name = "gateway"
    server_fallback = False
    pass_inference_geo = False

    def __init__(self, *, client_factory: ClientFactory | None = None) -> None:
        super().__init__(client_factory=client_factory or build_gateway_client)
