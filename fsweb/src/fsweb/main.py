"""ASGI application: ``fsweb.main:app`` (factory ``create_app``)."""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.requests import Request

from fsweb import __version__, errors
from fsweb.logging_setup import configure_logging
from fsweb.routers import (
    admin,
    ai,
    audit_log,
    auth,
    clients,
    compliance,
    engagements,
    files,
    fixplans,
    internal,
    misc,
    release,
    reviewnotes,
    runs,
    setup,
)
from fsweb.settings import Settings
from fsweb.state import AppState, build_state

log = logging.getLogger("fsweb.http")

# The UI's module scripts, stylesheets and pdf.js worker are served with `nosniff`, so their media types must not come
# from the Windows registry, where `.js` is often mapped to text/plain (the browser would then refuse to run the UI).
for _ext, _type in ((".js", "text/javascript"), (".mjs", "text/javascript"), (".css", "text/css"), (".svg", "image/svg+xml"),
                    (".json", "application/json"), (".woff2", "font/woff2"), (".map", "application/json"), (".html", "text/html")):
    mimetypes.add_type(_type, _ext)

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_CID = re.compile(r"^[A-Za-z0-9-]{8,64}$")

# identical to the policy Caddy sets in front of the UI (ops/caddy/Caddyfile); a test keeps the two in step
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; "
       "connect-src 'self'; worker-src 'self' blob:; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.requests = Counter("fsweb_http_requests_total", "HTTP requests", ["method", "route", "status"], registry=self.registry)
        self.latency = Histogram("fsweb_http_request_seconds", "HTTP latency", ["route"], registry=self.registry,
                                 buckets=(0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 1, 2, 5, 10))
        self.queue = Gauge("fsweb_job_queue_depth", "Queued jobs", ["queue"], registry=self.registry)
        self.runs = Gauge("fsweb_runs_running", "Runs in progress", registry=self.registry)
        self.emergency_reviews_overdue = Gauge("fsweb_itgc_emergency_reviews_overdue",
                                               "Emergency changes deployed more than 48 hours ago without approval (E-ITGC-01)", registry=self.registry)


class EdgeMiddleware:
    """Correlation id (E-OPS-02), security headers, access log and metrics in one pure-ASGI layer."""

    def __init__(self, app: ASGIApp, settings: Settings, metrics: Metrics) -> None:
        self.app = app
        self.settings = settings
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        cid = headers.get("x-correlation-id", "")
        if not _CID.match(cid):
            cid = uuid.uuid4().hex
        scope.setdefault("state", {})["correlation_id"] = cid
        start = time.perf_counter()
        status_holder = {"status": 500}
        path = scope.get("path", "")

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                hdrs = list(message.get("headers", []))
                extra = {
                    b"x-correlation-id": cid.encode(),
                    b"x-content-type-options": b"nosniff",
                    b"x-frame-options": b"DENY",
                    b"referrer-policy": b"no-referrer",
                    b"permissions-policy": b"camera=(), microphone=(), geolocation=(), payment=()",
                    b"cross-origin-opener-policy": b"same-origin",
                    b"content-security-policy": CSP.encode(),
                }
                if self.settings.cookie_secure:
                    extra[b"strict-transport-security"] = b"max-age=31536000; includeSubDomains"
                if path.startswith("/api/"):
                    extra.setdefault(b"cache-control", b"no-store")
                present = {k.lower() for k, _ in hdrs}
                for k, v in extra.items():
                    if k not in present:
                        hdrs.append((k, v))
                message["headers"] = hdrs
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - start
            route = scope.get("route")
            template = getattr(route, "path", None) or ("/static" if not path.startswith("/api") else "unmatched")
            if path.startswith("/api/v1") and template.startswith("/") and not template.startswith("/api/v1"):
                template = "/api/v1" + template  # routes are registered under the versioned prefix
            self.metrics.requests.labels(scope.get("method", ""), template, str(status_holder["status"])).inc()
            self.metrics.latency.labels(template).observe(elapsed)
            if not path.startswith("/metrics"):
                log.info("request", extra={"correlation_id": cid, "method": scope.get("method"), "path": template,
                                           "status": status_holder["status"], "duration_ms": round(elapsed * 1000, 1)})


LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost", "::1"})
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class LoopbackGuardMiddleware:
    """Desktop edition (``FSWEB_ENV=offline``): DNS-rebinding defence. Only ``Host: 127.0.0.1:<port>``,
    ``localhost:<port>`` or ``[::1]:<port>`` (the port this server listens on) is served, and a state-changing request
    that carries an ``Origin`` must come from one of those origins. A page on another site whose name was re-pointed at
    127.0.0.1 sends its own name as Host / Origin and is refused before any endpoint runs."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    @staticmethod
    def _port(scope: Scope) -> int | None:
        server = scope.get("server")
        return int(server[1]) if server and len(server) > 1 and server[1] else None

    @staticmethod
    def _loopback(netloc: str, port: int | None, *, scheme: str = "http") -> bool:
        try:
            parts = urlsplit(f"{scheme}://{netloc}")
            given = parts.port or (443 if scheme == "https" else 80)
        except ValueError:
            return False
        plain = not (parts.username or parts.path or parts.query or parts.fragment)
        return plain and parts.hostname in LOOPBACK_NAMES and (port is None or given == port)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        port = self._port(scope)
        if not self._loopback(headers.get("host", ""), port):
            await self._refuse(scope, send, 400, "invalid_host", "the desktop edition only answers to 127.0.0.1, localhost or [::1] on its own port")
            return
        origin = headers.get("origin")
        if scope.get("method", "GET") in UNSAFE_METHODS and origin is not None:
            parts = urlsplit(origin)
            if parts.scheme != "http" or not self._loopback(parts.netloc, port):
                await self._refuse(scope, send, 403, "origin_rejected", "cross-site request refused")
                return
        await self.app(scope, receive, send)

    @staticmethod
    async def _refuse(scope: Scope, send: Send, status: int, code: str, message: str) -> None:
        cid = scope.get("state", {}).get("correlation_id")
        body = json.dumps({"error": {"code": code, "message": message, "correlation_id": cid}}).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


# The SPA's hashed bundles never change under a name; index.html must be revalidated so an upgrade takes effect.
ASSET_CACHE = "public, max-age=31536000, immutable"
INDEX_CACHE = "no-cache"


def _otel(app: FastAPI) -> None:  # pragma: no cover - optional
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # type: ignore[import-not-found]

        FastAPIInstrumentor.instrument_app(app)
    except ImportError:
        log.warning("OpenTelemetry endpoint configured but opentelemetry-instrumentation-fastapi is not installed")


def create_app(settings: Settings | None = None, *, state: AppState | None = None) -> FastAPI:
    settings = settings or (state.settings if state else Settings.from_env())
    configure_logging(settings.log_level)
    st = state or build_state(settings)
    metrics = Metrics()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        st.db.dispose()

    app = FastAPI(
        title="FS-Platform API",
        version=__version__,
        description=("Ind AS financial statement preparation and review platform. Every endpoint requires an authenticated, "
                     "MFA-verified session (cookie `fs_session`) and the `X-CSRF-Token` header for unsafe methods, except the "
                     "login / invite / password-reset flows, the sign-in options, the desktop edition's first-run setup and the "
                     "internal render-worker protocol (`X-Worker-Token`)."),
        lifespan=lifespan,
        openapi_url="/api/v1/openapi.json",
        docs_url=None if settings.production_like else "/api/v1/docs",
        redoc_url=None,
    )
    app.state.fs = st
    app.state.metrics = metrics
    errors.install(app)
    # Desktop edition: no render workers (the in-process worker renders) and no metrics scraper. Their endpoints stay
    # closed: whoever could read the worker token could otherwise claim render jobs and download client workbooks.
    desktop = settings.env == "offline"
    api = APIRouter(prefix="/api/v1")
    for r in (auth, setup, admin, clients, engagements, files, runs, fixplans, reviewnotes, ai, release, audit_log, compliance, internal, misc):
        if desktop and r is internal:
            continue
        api.include_router(r.router)
    app.include_router(api)

    if desktop:

        @app.get("/metrics", include_in_schema=False)
        def no_metrics() -> PlainTextResponse:
            raise errors.not_found("metrics")

    else:

        @app.get("/metrics", include_in_schema=False)
        def prometheus_metrics() -> PlainTextResponse:
            from sqlalchemy import select

            from fsweb import metrics as global_metrics
            from fsweb.db import SYSTEM
            from fsweb.jobs.queue import queue_depth
            from fsweb.models import RenderWorker, Run
            from fsweb.services import compliance

            try:
                with st.db.scope(SYSTEM) as s:
                    for q, n in queue_depth(s).items():
                        metrics.queue.labels(q).set(n)
                    metrics.runs.set(len(s.execute(select(Run.id).where(Run.status == "running")).all()))
                    metrics.emergency_reviews_overdue.set(len(compliance.overdue_emergency_reviews(s)))
                    # Heartbeats may have reached another API node: refresh the gauge from the database.
                    for w in s.execute(select(RenderWorker)).scalars():
                        global_metrics.render_heartbeat(w.id, w.last_heartbeat_at)
            except Exception:
                log.warning("queue depth unavailable")
            body = generate_latest(metrics.registry) + generate_latest(global_metrics.REGISTRY)
            return PlainTextResponse(body.decode(), media_type=CONTENT_TYPE_LATEST)

    if settings.allowed_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins, allow_credentials=True,
                           allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key", "If-Match", "X-Action-Reason", "X-Correlation-ID"])

    if settings.serve_ui and Path(settings.serve_ui, "index.html").exists():
        ui_root = Path(settings.serve_ui).resolve()
        index_html = ui_root / "index.html"

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            if path == "api" or path.startswith("api/"):
                raise errors.not_found("API endpoint")  # an unknown API path is an error, never the app shell
            target = (ui_root / path).resolve()
            if path and ui_root in target.parents and target.is_file() and target != index_html:
                return FileResponse(target, headers={"Cache-Control": ASSET_CACHE} if path.startswith("assets/") else None)
            if path.startswith("assets/"):
                raise errors.not_found("file")  # a stale page asking for an old bundle must not receive index.html
            return FileResponse(index_html, headers={"Cache-Control": INDEX_CACHE})

    if desktop:
        app.add_middleware(LoopbackGuardMiddleware)
    app.add_middleware(EdgeMiddleware, settings=settings, metrics=metrics)
    _otel(app)
    return app


def _lazy_app() -> FastAPI:
    return create_app()


class _LazyApp:
    """``uvicorn fsweb.main:app`` builds the app on first use (settings from the environment)."""

    _app: FastAPI | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self._app is None:
            self._app = _lazy_app()
        await self._app(scope, receive, send)


app = _LazyApp()
_ = Request
