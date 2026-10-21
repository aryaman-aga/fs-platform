"""HTTP client for the internal render-worker protocol (server side: fsweb).

Endpoints (header ``X-Worker-Token``)::

    POST /api/v1/internal/render/heartbeat          {worker_id, excel_build}
    POST /api/v1/internal/render/claim              {worker_id, excel_build} -> 200 job | 204
    GET  /api/v1/internal/render/{job_id}/input     -> xlsx bytes
    POST /api/v1/internal/render/{job_id}/complete  multipart: pdf file + form field meta (JSON)
    POST /api/v1/internal/render/{job_id}/fail      {error}

Transport errors, 429 and 5xx are retried with exponential backoff; other 4xx
are raised immediately (they mean the request itself is wrong).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

PREFIX = "/api/v1/internal/render"


class ApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class RenderJob:
    job_id: str
    run_id: str | None = None
    mode: str = "draft"
    sheets: list[str] | None = None
    options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RenderJob:
        if not isinstance(data, dict) or not data.get("job_id"):
            raise ApiError("claim response has no job_id")
        sheets = data.get("sheets")
        return cls(
            job_id=str(data["job_id"]),
            run_id=str(data["run_id"]) if data.get("run_id") is not None else None,
            mode=str(data.get("mode") or "draft"),
            sheets=[str(s) for s in sheets] if isinstance(sheets, list) and sheets else None,
            options=dict(data.get("options") or {}),
        )


class RenderApiClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout_s: float = 120.0,
        verify: bool | str = True,
        transport: httpx.BaseTransport | None = None,
        max_attempts: int = 4,
        backoff_base_s: float = 1.0,
        max_backoff_s: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"X-Worker-Token": token, "User-Agent": "fsrender-win/1.0"},
            timeout=timeout_s,
            verify=verify,
            transport=transport,
            follow_redirects=False,
        )
        self._max_attempts = max(1, max_attempts)
        self._backoff_base_s = backoff_base_s
        self._max_backoff_s = max_backoff_s
        self._sleep = sleep

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> RenderApiClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ helpers
    def _backoff(self, attempt: int) -> None:
        self._sleep(min(self._max_backoff_s, self._backoff_base_s * (2 ** (attempt - 1))))

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._client.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                last_error = exc
            else:
                if response.status_code == 429 or response.status_code >= 500:
                    last_error = ApiError(f"{method} {path} -> HTTP {response.status_code}", response.status_code)
                elif response.status_code >= 400:
                    raise ApiError(f"{method} {path} -> HTTP {response.status_code}", response.status_code)
                else:
                    return response
            if attempt < self._max_attempts:
                self._backoff(attempt)
        if isinstance(last_error, ApiError):
            raise last_error
        raise ApiError(f"{method} {path} failed: {type(last_error).__name__}") from last_error

    # ------------------------------------------------------------------ protocol
    def heartbeat(self, worker_id: str, excel_build: str) -> None:
        self._request("POST", f"{PREFIX}/heartbeat", json={"worker_id": worker_id, "excel_build": excel_build})

    def claim(self, worker_id: str, excel_build: str) -> RenderJob | None:
        response = self._request("POST", f"{PREFIX}/claim", json={"worker_id": worker_id, "excel_build": excel_build})
        if response.status_code == 204 or not response.content:
            return None
        return RenderJob.from_json(response.json())

    def download_input(self, job_id: str, dest: Path, *, max_bytes: int = 512 * 1024 * 1024) -> int:
        path = f"{PREFIX}/{job_id}/input"
        for attempt in range(1, self._max_attempts + 1):
            try:
                written = 0
                with self._client.stream("GET", path) as response:
                    if response.status_code == 429 or response.status_code >= 500:
                        raise ApiError(f"GET {path} -> HTTP {response.status_code}", response.status_code)
                    if response.status_code >= 400:
                        raise ApiError(f"GET {path} -> HTTP {response.status_code}", response.status_code)
                    with dest.open("wb") as handle:
                        for chunk in response.iter_bytes():
                            written += len(chunk)
                            if written > max_bytes:
                                raise ApiError(f"input exceeds {max_bytes} bytes; refusing")
                            handle.write(chunk)
                return written
            except ApiError as exc:
                if exc.status_code is None or (exc.status_code < 500 and exc.status_code != 429) or attempt == self._max_attempts:
                    raise
            except httpx.TransportError as exc:
                if attempt == self._max_attempts:
                    raise ApiError(f"GET {path} failed: {type(exc).__name__}") from exc
            self._backoff(attempt)
        raise ApiError(f"GET {path} failed")  # pragma: no cover - loop always returns or raises

    def complete(self, job_id: str, pdf_path: Path, meta: dict[str, Any]) -> None:
        pdf_bytes = pdf_path.read_bytes()
        self._request(
            "POST",
            f"{PREFIX}/{job_id}/complete",
            files={"pdf": ("output.pdf", pdf_bytes, "application/pdf")},
            data={"meta": json.dumps(meta, sort_keys=True)},
        )

    def fail(self, job_id: str, error: str) -> None:
        self._request("POST", f"{PREFIX}/{job_id}/fail", json={"error": error[:500]})
