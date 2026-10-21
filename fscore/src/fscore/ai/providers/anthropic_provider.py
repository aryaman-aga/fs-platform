"""Claude through the official ``anthropic`` SDK (02 §9.3).

Platforms (same SDK surface after construction):

* ``anthropic`` — Anthropic API, ``anthropic.Anthropic()``; region = ``inference_geo`` (us | global).
* ``bedrock`` — ``anthropic.AnthropicBedrockMantle(aws_region=…)``, model ``anthropic.claude-opus-5-5``.
* ``vertex`` — ``anthropic.AnthropicVertex(project_id=…, region=…)``.
* ``foundry`` — ``anthropic.AnthropicFoundry(resource=…, api_key=…)``.

Every request streams (``.stream()`` + ``get_final_message()``) with
structured output (``output_config.format`` from ``anthropic.transform_schema``)
and an explicit effort level. Thinking is left at the model's adaptive default.

Refusal fallback is ON by default (``FSCORE_AI_REFUSAL_FALLBACK=0`` turns it
off): on the Anthropic API the server-side ``fallbacks="default"`` parameter
(beta ``server-side-fallback-2026-07-01``); on Bedrock, Vertex and Foundry the
SDK's client-side ``BetaRefusalFallbackMiddleware`` with one
``BetaFallbackState`` per request. The stop reason is checked before any
content is read: a refusal or a ``max_tokens`` stop returns no content.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fscore.ai.provider import ProviderRequest, RawCompletion
from fscore.ai.settings import FALLBACK_MODEL, ProviderSettings, get_secret

SERVER_FALLBACK_BETA = "server-side-fallback-2026-07-01"

ClientFactory = Callable[[ProviderSettings], Any]


def build_client(settings: ProviderSettings) -> Any:
    """Construct the platform client. Keys come from env / OS keyring only."""
    import anthropic

    common: dict[str, Any] = {"timeout": settings.timeout_s, "max_retries": settings.max_retries}
    middleware = None
    if settings.provider in FALLBACK_MODEL and settings.refusal_fallback:
        middleware = [anthropic.BetaRefusalFallbackMiddleware([{"model": settings.fallback_model or FALLBACK_MODEL[settings.provider]}])]
    if settings.provider == "anthropic":
        key = get_secret("anthropic_api_key", "ANTHROPIC_API_KEY")
        return anthropic.Anthropic(api_key=key, **common) if key else anthropic.Anthropic(**common)
    if settings.provider == "bedrock":
        return anthropic.AnthropicBedrockMantle(aws_region=settings.region, middleware=middleware, **common)
    if settings.provider == "vertex":
        if not settings.vertex_project_id or not settings.region:
            raise ValueError("Vertex AI needs a project id and a region")
        return anthropic.AnthropicVertex(project_id=settings.vertex_project_id, region=settings.region, middleware=middleware, **common)
    if settings.provider == "foundry":
        key = get_secret("foundry_api_key", "ANTHROPIC_FOUNDRY_API_KEY")
        kwargs: dict[str, Any] = {"resource": settings.foundry_resource, "middleware": middleware, **common}
        if key:
            kwargs["api_key"] = key
        return anthropic.AnthropicFoundry(**kwargs)
    raise ValueError(f"not an Anthropic platform: {settings.provider}")


def to_raw(message: Any) -> RawCompletion:
    """Map a final (Beta)Message to a RawCompletion — stop reason first, content last."""
    usage_obj = getattr(message, "usage", None)
    usage: dict[str, int] = {}
    for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
        v = getattr(usage_obj, k, None)
        if isinstance(v, int):
            usage[k] = v
    iterations = getattr(usage_obj, "iterations", None) or []
    fallback_ran = any(getattr(e, "type", None) == "fallback_message" for e in iterations)
    raw = RawCompletion(
        stop_reason=getattr(message, "stop_reason", None),
        request_id=getattr(message, "_request_id", None),
        served_model=getattr(message, "model", None),
        fallback_ran=fallback_ran,
        usage=usage,
    )
    if raw.stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        raw.stop_category = getattr(details, "category", None) if details is not None else None
        return raw  # empty or partial content is discarded
    if raw.stop_reason == "max_tokens":
        return raw  # truncated output is never used
    texts = [b.text for b in getattr(message, "content", []) if getattr(b, "type", None) == "text"]
    raw.text = "".join(texts) if texts else None
    return raw


class AnthropicProvider:
    name = "anthropic"
    server_fallback = True  # use fallbacks="default" on the Anthropic API
    pass_inference_geo = True

    def __init__(self, *, client_factory: ClientFactory | None = None) -> None:
        self._factory = client_factory or build_client

    def request_kwargs(self, request: ProviderRequest, settings: ProviderSettings) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": settings.platform_model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": request.user}],
            "output_config": {
                "effort": request.effort,
                "format": {"type": "json_schema", "schema": request.output_schema},
            },
        }
        if settings.provider == "anthropic":
            if self.pass_inference_geo and settings.region:
                kwargs["inference_geo"] = settings.region
            if self.server_fallback and settings.refusal_fallback:
                kwargs["betas"] = [SERVER_FALLBACK_BETA]
                kwargs["fallbacks"] = "default"
        return kwargs

    def send(self, request: ProviderRequest, settings: ProviderSettings) -> RawCompletion:
        import anthropic

        client = self._factory(settings)
        kwargs = self.request_kwargs(request, settings)
        try:
            if settings.provider in FALLBACK_MODEL and settings.refusal_fallback:
                state = anthropic.BetaFallbackState()  # one per conversation; each request is its own
                with state:
                    message = self._stream(client, kwargs)
            else:
                message = self._stream(client, kwargs)
        except anthropic.AuthenticationError as exc:
            return RawCompletion(error=f"AuthenticationError:{exc.status_code}")
        except anthropic.PermissionDeniedError as exc:
            return RawCompletion(error=f"PermissionDeniedError:{exc.status_code}")
        except anthropic.NotFoundError as exc:
            return RawCompletion(error=f"NotFoundError:{exc.status_code}")
        except anthropic.RateLimitError as exc:
            return RawCompletion(error=f"RateLimitError:{exc.status_code}")
        except anthropic.BadRequestError as exc:
            return RawCompletion(error=f"BadRequestError:{exc.status_code}")
        except anthropic.APIStatusError as exc:
            return RawCompletion(error=f"APIStatusError:{exc.status_code}")
        except anthropic.APITimeoutError:
            return RawCompletion(error="APITimeoutError")
        except anthropic.APIConnectionError:
            return RawCompletion(error="APIConnectionError")
        return to_raw(message)

    @staticmethod
    def _stream(client: Any, kwargs: dict[str, Any]) -> Any:
        with client.beta.messages.stream(**kwargs) as stream:
            return stream.get_final_message()
