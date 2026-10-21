"""Fully offline local model (Ollama or an OpenAI-compatible server on localhost).

For firms that forbid external AI (02 §9.3). This plugin talks only to
localhost — any other host, and any redirect away from localhost, is refused —
and it never routes Claude calls. Structured output is requested with the
same JSON schema and validated the same way as for Claude.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse

from fscore.ai.errors import AIConfigError
from fscore.ai.provider import ProviderRequest, RawCompletion
from fscore.ai.settings import ProviderSettings, is_localhost

Opener = Callable[[urllib.request.Request, float], Any]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        raise HTTPError(newurl, code, "redirects are not followed by the local provider", headers, fp)


def _default_opener(req: urllib.request.Request, timeout: float) -> Any:
    opener = urllib.request.build_opener(_NoRedirect())
    return opener.open(req, timeout=timeout)


class LocalProvider:
    name = "local"

    def __init__(self, *, opener: Opener | None = None) -> None:
        self._open = opener or _default_opener

    def send(self, request: ProviderRequest, settings: ProviderSettings) -> RawCompletion:
        endpoint = settings.endpoint or ""
        if urlparse(endpoint).scheme not in ("http", "https") or not is_localhost(endpoint):
            raise AIConfigError("the local provider only talks to localhost")
        model = settings.local_model or settings.model
        if model.lower().startswith("claude"):
            raise AIConfigError("the local provider must not route Claude calls")
        messages = [{"role": "system", "content": request.system}, {"role": "user", "content": request.user}]
        if settings.local_flavour == "ollama":
            url = endpoint.rstrip("/") + "/api/chat"
            body: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "stream": False,
                "format": request.output_schema,
                "options": {"num_predict": request.max_tokens},
            }
        else:
            url = endpoint.rstrip("/") + "/v1/chat/completions"
            body = {
                "model": model,
                "messages": messages,
                "max_tokens": request.max_tokens,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": request.output_model_name, "schema": request.output_schema, "strict": True},
                },
            }
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")  # noqa: S310 - URL validated as localhost http(s) above
        try:
            with self._open(req, settings.timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            return RawCompletion(error=f"HTTPError:{exc.code}")
        except (URLError, TimeoutError, OSError):
            return RawCompletion(error="ConnectionError")
        except json.JSONDecodeError:
            return RawCompletion(error="InvalidResponse")
        if settings.local_flavour == "ollama":
            text = (data.get("message") or {}).get("content")
            done = data.get("done_reason", "stop")
            usage = {"input_tokens": int(data.get("prompt_eval_count", 0)), "output_tokens": int(data.get("eval_count", 0))}
        else:
            choice = (data.get("choices") or [{}])[0]
            text = (choice.get("message") or {}).get("content")
            done = choice.get("finish_reason", "stop")
            u = data.get("usage") or {}
            usage = {"input_tokens": int(u.get("prompt_tokens", 0)), "output_tokens": int(u.get("completion_tokens", 0))}
        if done == "length":
            return RawCompletion(stop_reason="max_tokens", served_model=model, usage=usage)
        return RawCompletion(text=text, stop_reason="end_turn", served_model=model, usage=usage)
