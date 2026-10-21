"""ASGI application: ``fsweb.main:app`` (factory ``create_app``)."""

from __future__ import annotations

import logging
import os
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

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
)
from fsweb.settings import Settings
from fsweb.state import AppState, build_state

log = logging.getLogger("fsweb.http")

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
                     "login / invite / password-reset flows and the internal render-worker protocol (`X-Worker-Token`)."),
        lifespan=lifespan,
        openapi_url="/api/v1/openapi.json",
        docs_url=None if settings.production_like else "/api/v1/docs",
        redoc_url=None,
    )
    app.state.fs = st
    app.state.metrics = metrics
    errors.install(app)
    api = APIRouter(prefix="/api/v1")
    for r in (auth, admin, clients, engagements, files, runs, fixplans, reviewnotes, ai, release, audit_log, compliance, internal, misc):
        api.include_router(r.router)
    app.include_router(api)

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

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            target = (ui_root / path).resolve()
            if path and ui_root in target.parents and target.is_file():
                return FileResponse(target)
            return FileResponse(ui_root / "index.html")

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
