"""Fake render API (in-memory stand-in for fsweb's internal render endpoints)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

TOKEN = "test-worker-token-0123456789"
MINIMAL_PDF = b"%PDF-1.7\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n" + b" " * 64


@dataclass
class FakeRenderServer:
    """In-memory stand-in for fsweb's /api/v1/internal/render endpoints."""

    jobs: list[dict[str, Any]] = field(default_factory=list)
    inputs: dict[str, bytes] = field(default_factory=dict)
    heartbeats: list[dict[str, Any]] = field(default_factory=list)
    claims: list[dict[str, Any]] = field(default_factory=list)
    completed: dict[str, dict[str, Any]] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)
    fail_next: dict[str, list[int]] = field(default_factory=dict)  # path suffix -> status codes to return first
    requests: list[httpx.Request] = field(default_factory=list)

    def add_job(self, job_id: str, data: bytes = b"PK\x03\x04fake-xlsx", **extra: Any) -> None:
        self.jobs.append({"job_id": job_id, "run_id": "run-" + job_id, "mode": "draft", "sheets": ["BS", "PL"], "options": {}, **extra})
        self.inputs[job_id] = data

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("X-Worker-Token") != TOKEN:
            return httpx.Response(401, json={"error": {"code": "unauthorised"}})
        path = request.url.path
        assert path.startswith("/api/v1/internal/render/"), path
        suffix = path.removeprefix("/api/v1/internal/render/")
        key = suffix.split("/")[-1]
        queued = self.fail_next.get(key)
        if queued:
            return httpx.Response(queued.pop(0))
        if suffix == "heartbeat":
            self.heartbeats.append(json.loads(request.content))
            return httpx.Response(204)
        if suffix == "claim":
            self.claims.append(json.loads(request.content))
            if not self.jobs:
                return httpx.Response(204)
            return httpx.Response(200, json=self.jobs.pop(0))
        job_id, action = suffix.split("/", 1)
        if action == "input":
            return httpx.Response(200, content=self.inputs[job_id])
        if action == "complete":
            body = request.content
            content_type = request.headers["content-type"]
            assert content_type.startswith("multipart/form-data")
            boundary = content_type.split("boundary=")[1].encode()
            parts = [p for p in body.split(b"--" + boundary) if b"Content-Disposition" in p]
            record: dict[str, Any] = {}
            for part in parts:
                head, _, payload = part.partition(b"\r\n\r\n")
                payload = payload.rstrip(b"\r\n")
                if b'name="pdf"' in head:
                    record["pdf"] = payload
                elif b'name="meta"' in head:
                    record["meta"] = json.loads(payload)
            self.completed[job_id] = record
            return httpx.Response(204)
        if action == "fail":
            self.failed[job_id] = json.loads(request.content)["error"]
            return httpx.Response(204)
        return httpx.Response(404)

