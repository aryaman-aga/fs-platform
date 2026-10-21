"""Process-wide Prometheus metrics used by the alert rules (E-OPS-01/04).

HTTP metrics live on each app instance (``main.Metrics``); the metrics below are
process-global because runs finish in workers (and, for Windows renders, in the
API) and heartbeats arrive on any API node. ``/metrics`` exposes both; a
standalone worker can expose them with ``--metrics-port``.
"""

from __future__ import annotations

from datetime import datetime

from prometheus_client import CollectorRegistry, Gauge, Histogram

REGISTRY = CollectorRegistry()

RUN_DURATION = Histogram(
    "fsweb_run_duration_seconds",
    "Run duration from start to finish, by run kind (SLO: check p95 < 60 s, render p95 < 90 s)",
    ["kind"],
    registry=REGISTRY,
    buckets=(1, 5, 10, 20, 30, 45, 60, 90, 120, 180, 300, 600, 1800),
)

RENDER_WORKER_HEARTBEAT = Gauge(
    "fsweb_render_worker_last_heartbeat_timestamp_seconds",
    "Unix time of the last heartbeat from each Windows render worker",
    ["worker_id"],
    registry=REGISTRY,
)

RUN_KINDS = ("check", "plan", "fix", "narrative", "render", "ai_review", "prepare", "diff", "pdf_check")


def observe_run(kind: str, started_at: datetime | None, finished_at: datetime | None) -> None:
    if started_at is None or finished_at is None:
        return
    RUN_DURATION.labels(kind if kind in RUN_KINDS else "other").observe(max(0.0, (finished_at - started_at).total_seconds()))


def render_heartbeat(worker_id: str, at: datetime) -> None:
    RENDER_WORKER_HEARTBEAT.labels(worker_id).set(at.timestamp())
