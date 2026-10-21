"""Providers: none (offline), Anthropic platforms via a fake SDK client, local, gateway."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest

from fscore.ai import AIConfigError, AIDisabledError, approve_request, prepare_request, send_request
from fscore.ai.provider import ProviderRequest, get_provider
from fscore.ai.providers import anthropic_provider as ap
from fscore.ai.providers.anthropic_provider import SERVER_FALLBACK_BETA, AnthropicProvider, build_client
from fscore.ai.providers.gateway import GatewayProvider, build_gateway_client
from fscore.ai.providers.local import LocalProvider
from fscore.ai.providers.mock import MockProvider
from fscore.ai.settings import ProviderSettings, get_secret, resolve_settings
from fscore.config import AIConfig
from fscore.models import Certainty, Finding, Severity

SECRET = "unit-test-secret"
TRIAGE_OK = json.dumps({"items": [{
    "finding_id": "F-0001", "classification": "genuine", "reason": "Total does not cast.",
    "suggested_wording": None, "cited_numbers": [{"ref": "Note 14-19!D25", "value": "1,234.56"}],
}]})


def _finding() -> Finding:
    return Finding(id="F-0001", rule_id="CAST-01", severity=Severity.HIGH, certainty=Certainty.CERTAIN, sheet="Note 14-19",
                   cells=["D25"], caption="Total trade payables", printed="1,234.56", expected="1,234.57",
                   difference=Decimal("-0.01"), explanation="Printed total does not cast.")


def _message(text: str | None = TRIAGE_OK, *, stop: str = "end_turn", category: str | None = None,
             model: str = "claude-opus-5-5", iterations: list[Any] | None = None) -> Any:
    content = [] if text is None else [SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(
        stop_reason=stop,
        stop_details=SimpleNamespace(category=category) if stop == "refusal" else None,
        model=model,
        _request_id="req_test_1",
        usage=SimpleNamespace(input_tokens=1200, output_tokens=300, cache_creation_input_tokens=0, cache_read_input_tokens=0,
                              iterations=iterations),
        content=content,
    )


class FakeStream:
    def __init__(self, message: Any) -> None:
        self.message = message

    def __enter__(self) -> FakeStream:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def get_final_message(self) -> Any:
        return self.message


class FakeClient:
    def __init__(self, result: Any) -> None:
        self.calls: list[dict[str, Any]] = []
        self._result = result
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs: Any) -> FakeStream:
        self.calls.append(kwargs)
        if isinstance(self._result, Exception):
            raise self._result
        return FakeStream(self._result)


def _send(cfg: AIConfig, client: FakeClient, provider_cls: type[AnthropicProvider] = AnthropicProvider) -> Any:
    p = prepare_request([_finding()], purpose="triage", config=cfg, context={"requested_by": "staff@firm"})
    token, _ = approve_request(p, approver="manager@firm", secret=SECRET)
    return send_request(p, token, config=cfg, secret=SECRET, provider=provider_cls(client_factory=lambda _s: client))


# ------------------------------------------------------------------ provider none


def test_provider_none_sends_nothing(no_network: None) -> None:
    cfg = AIConfig()  # provider none is the default
    p = prepare_request([_finding()], purpose="triage", config=cfg, context={"requested_by": "a"})
    token, _ = approve_request(p, approver="b", secret=SECRET)
    prov = MockProvider()
    res = send_request(p, token, config=cfg, secret=SECRET, provider=prov)
    assert res.status == "disabled"
    assert not res.ok
    assert prov.calls == []
    with pytest.raises(AIDisabledError):
        get_provider(ProviderSettings(provider="none", model="claude-opus-5-5", region=None)).send(
            ProviderRequest(system="", user="", output_schema={}, output_model_name="X", max_tokens=1, effort="high", payload_sha256="0" * 64),
            ProviderSettings(provider="none", model="claude-opus-5-5", region=None),
        )


def test_importing_ai_package_loads_no_network_modules() -> None:
    code = (
        "import sys\n"
        "from fscore.config import AIConfig\n"
        "from fscore.ai import prepare_request\n"
        "prepare_request([], purpose='triage', config=AIConfig())\n"
        "print(any(m in sys.modules for m in ('anthropic', 'httpx', 'httpx2', 'urllib3', 'requests')))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


# ------------------------------------------------------------------ Anthropic platforms


def test_anthropic_api_request_shape(no_network: None) -> None:
    client = FakeClient(_message())
    res = _send(AIConfig(provider="anthropic", region="global"), client)
    assert res.ok, res.message
    kw = client.calls[0]
    assert kw["model"] == "claude-opus-5-5"
    assert kw["output_config"]["effort"] == "high"
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["output_config"]["format"]["schema"]["additionalProperties"] is False
    assert kw["betas"] == [SERVER_FALLBACK_BETA]
    assert kw["fallbacks"] == "default"
    assert kw["inference_geo"] == "global"
    assert "thinking" not in kw  # adaptive thinking is the model default
    assert "<payload>" in kw["messages"][0]["content"]
    assert "Ind AS" in kw["system"]
    assert res.request_id == "req_test_1"
    assert res.usage["input_tokens"] == 1200
    assert Decimal(res.cost_usd) == (Decimal(1200) * 4 + Decimal(300) * 20) / Decimal(1_000_000)
    assert res.prompt_id == "fs-triage"
    assert res.prompt_version
    assert res.proposals[0].verified


def test_bedrock_uses_prefixed_model_and_client_side_fallback(monkeypatch: pytest.MonkeyPatch, no_network: None) -> None:
    entered: list[str] = []

    class FakeState:
        def __enter__(self) -> FakeState:
            entered.append("in")
            return self

        def __exit__(self, *exc: object) -> None:
            entered.append("out")

    monkeypatch.setattr(anthropic, "BetaFallbackState", FakeState)
    client = FakeClient(_message())
    res = _send(AIConfig(provider="bedrock", region="ap-south-1"), client)
    assert res.ok, res.message
    kw = client.calls[0]
    assert kw["model"] == "anthropic.claude-opus-5-5"
    assert "fallbacks" not in kw
    assert "betas" not in kw
    assert "inference_geo" not in kw
    assert entered == ["in", "out"]


def test_build_client_per_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, dict[str, Any]] = {}

    def recorder(name: str) -> Any:
        def make(**kw: Any) -> str:
            seen[name] = kw
            return name

        return make

    for cls in ("Anthropic", "AnthropicBedrockMantle", "AnthropicVertex", "AnthropicFoundry"):
        monkeypatch.setattr(anthropic, cls, recorder(cls))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", "foundry-test")
    monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "fs-proj")
    assert build_client(resolve_settings(AIConfig(provider="anthropic", region="us"))) == "Anthropic"
    assert seen["Anthropic"]["api_key"] == "sk-test"
    build_client(resolve_settings(AIConfig(provider="bedrock", region="ap-south-1")))
    assert seen["AnthropicBedrockMantle"]["aws_region"] == "ap-south-1"
    mw = seen["AnthropicBedrockMantle"]["middleware"]
    assert isinstance(mw[0], anthropic.BetaRefusalFallbackMiddleware)
    build_client(resolve_settings(AIConfig(provider="vertex", region="asia-south1")))
    assert seen["AnthropicVertex"]["project_id"] == "fs-proj"
    assert seen["AnthropicVertex"]["region"] == "asia-south1"
    build_client(resolve_settings(AIConfig(provider="foundry", region="centralindia", endpoint="fs-foundry")))
    assert seen["AnthropicFoundry"]["resource"] == "fs-foundry"
    assert seen["AnthropicFoundry"]["api_key"] == "foundry-test"
    monkeypatch.setenv("FSCORE_AI_REFUSAL_FALLBACK", "0")
    build_client(resolve_settings(AIConfig(provider="bedrock", region="ap-south-1")))
    assert seen["AnthropicBedrockMantle"]["middleware"] is None


def test_refusal_and_max_tokens_yield_no_content(no_network: None) -> None:
    refused = _send(AIConfig(provider="anthropic", region="global"), FakeClient(_message(None, stop="refusal", category="cyber")))
    assert refused.status == "refused"
    assert refused.output is None
    assert refused.stop_category == "cyber"
    truncated = _send(AIConfig(provider="anthropic", region="global"), FakeClient(_message('{"items": [{"finding_id"', stop="max_tokens")))
    assert truncated.status == "max_tokens"
    assert truncated.output is None
    assert truncated.proposals == []


def test_invalid_structured_output_is_discarded(no_network: None) -> None:
    bad = json.dumps({"items": [{"finding_id": "F-0001", "classification": "maybe", "reason": "x", "suggested_wording": None, "cited_numbers": []}]})
    res = _send(AIConfig(provider="anthropic", region="global"), FakeClient(_message(bad)))
    assert res.status == "invalid_output"
    assert res.output is None
    assert res.response_sha256


def test_fallback_served_response_is_recorded(no_network: None) -> None:
    iters = [SimpleNamespace(type="message"), SimpleNamespace(type="fallback_message")]
    res = _send(AIConfig(provider="anthropic", region="global"), FakeClient(_message(model="claude-opus-4-8", iterations=iters)))
    assert res.ok
    assert res.fallback_ran
    assert res.served_model == "claude-opus-4-8"


def test_api_errors_are_reported_without_content(no_network: None) -> None:
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx2.Response(429, request=req, json={"error": {"type": "rate_limit_error", "message": "slow down"}})
    res = _send(AIConfig(provider="anthropic", region="global"), FakeClient(anthropic.RateLimitError("rate limited", response=resp, body=None)))
    assert res.status == "error"
    assert res.message == "RateLimitError:429"
    res2 = _send(AIConfig(provider="anthropic", region="global"), FakeClient(anthropic.APIConnectionError(request=req)))
    assert res2.status == "error"
    assert res2.message == "APIConnectionError"


def test_unapproved_model_is_refused() -> None:
    cfg = AIConfig(provider="anthropic", region="global", model="claude-opus-4-1")
    p = prepare_request([_finding()], purpose="triage", config=cfg, context={"requested_by": "s"})
    token, _ = approve_request(p, approver="m", secret=SECRET)
    client = FakeClient(_message())
    res = send_request(p, token, config=cfg, secret=SECRET, provider=AnthropicProvider(client_factory=lambda _s: client))
    assert res.status == "config_error"
    assert client.calls == []


# ------------------------------------------------------------------ gateway


def test_gateway_requires_https_and_token(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(AIConfigError):
        resolve_settings(AIConfig(provider="gateway", region="in", endpoint="http://ai-gw.firm.internal"))
    s = resolve_settings(AIConfig(provider="gateway", region="in", endpoint="https://ai-gw.firm.internal/anthropic"))
    monkeypatch.delenv("FSCORE_AI_GATEWAY_TOKEN", raising=False)
    monkeypatch.setattr(ap, "get_secret", lambda *a, **k: None)
    import fscore.ai.providers.gateway as gw

    monkeypatch.setattr(gw, "get_secret", lambda *a, **k: None)
    with pytest.raises(AIConfigError):
        build_gateway_client(s)
    seen: dict[str, Any] = {}
    monkeypatch.setattr(gw, "get_secret", lambda *a, **k: "sso-token")
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: seen.update(kw) or "client")
    assert build_gateway_client(s) == "client"
    assert seen["base_url"] == "https://ai-gw.firm.internal/anthropic"
    assert seen["auth_token"] == "sso-token"


def test_gateway_sends_no_fallback_params(no_network: None) -> None:
    client = FakeClient(_message())
    res = _send(AIConfig(provider="gateway", region="in", endpoint="https://ai-gw.firm.internal"), client, GatewayProvider)
    assert res.ok
    kw = client.calls[0]
    assert "fallbacks" not in kw
    assert "inference_geo" not in kw


# ------------------------------------------------------------------ local


def test_local_provider_only_localhost_and_never_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(AIConfigError):
        resolve_settings(AIConfig(provider="local", endpoint="http://10.1.2.3:11434"), env={"FSCORE_AI_LOCAL_MODEL": "llama"})
    with pytest.raises(AIConfigError):
        resolve_settings(AIConfig(provider="local", endpoint="http://127.0.0.1:11434"), env={"FSCORE_AI_LOCAL_MODEL": "claude-opus-5-5"})
    s = resolve_settings(AIConfig(provider="local", endpoint="http://127.0.0.1:11434"), env={"FSCORE_AI_LOCAL_MODEL": "llama3.3"})
    assert s.model == "llama3.3"
    bad = s.model_copy(update={"endpoint": "https://example.com"})
    with pytest.raises(AIConfigError):
        LocalProvider(opener=lambda *_: None).send(
            ProviderRequest(system="s", user="u", output_schema={}, output_model_name="X", max_tokens=10, effort="high", payload_sha256="0" * 64), bad
        )


class _Resp(io.BytesIO):
    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_local_provider_ollama_and_openai_flavours(no_network: None) -> None:
    seen: list[Any] = []

    def ollama(req: Any, timeout: float) -> _Resp:
        seen.append(req)
        return _Resp(json.dumps({"message": {"content": TRIAGE_OK}, "done_reason": "stop", "prompt_eval_count": 9, "eval_count": 4}).encode())

    cfg = AIConfig(provider="local", endpoint="http://127.0.0.1:11434")
    s = resolve_settings(cfg, env={"FSCORE_AI_LOCAL_MODEL": "llama3.3"})
    from fscore.ai.payload import build_preview
    from fscore.ai.review import execute

    p = build_preview([_finding()], purpose="triage", config=cfg, settings=s)
    res = execute(p, LocalProvider(opener=ollama), s)
    assert res.ok, res.message
    assert seen[0].full_url == "http://127.0.0.1:11434/api/chat"
    body = json.loads(seen[0].data)
    assert body["format"]["type"] == "object"
    assert body["model"] == "llama3.3"

    def openai(req: Any, timeout: float) -> _Resp:
        seen.append(req)
        return _Resp(json.dumps({"choices": [{"message": {"content": TRIAGE_OK}, "finish_reason": "length"}], "usage": {"prompt_tokens": 9}}).encode())

    s2 = resolve_settings(cfg, env={"FSCORE_AI_LOCAL_MODEL": "qwen", "FSCORE_AI_LOCAL_FLAVOUR": "openai"})
    res2 = execute(p, LocalProvider(opener=openai), s2)
    assert res2.status == "max_tokens"
    assert seen[1].full_url.endswith("/v1/chat/completions")
    assert json.loads(seen[1].data)["response_format"]["type"] == "json_schema"


def test_secrets_come_from_env_not_config() -> None:
    assert get_secret("anthropic_api_key", "ANTHROPIC_API_KEY", env={"ANTHROPIC_API_KEY": "k"}) == "k"
    cfg = AIConfig(provider="anthropic", region="global")
    assert "api_key" not in cfg.model_dump()
