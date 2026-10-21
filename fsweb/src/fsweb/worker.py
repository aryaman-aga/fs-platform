"""Job worker: ``python -m fsweb.worker [--queues default] [--once] [--metrics-port 9101]``.

Run one or more workers for the ``default`` queue (checks, plans, fixes,
renders without a Windows worker, diffs, preparation) and exactly one process
with ``--queues ai-gateway`` on the only host that is allowed internet egress.
Workers process each job in an isolated temp directory and run with no
outbound network (enforced by the deployment's firewall / namespace).
The offline launcher (Mode C) runs ``run_forever`` in a thread instead.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import threading
import time
from datetime import timedelta

from fsweb.jobs.handlers import process
from fsweb.jobs.queue import claim, reap_stale
from fsweb.logging_setup import configure_logging
from fsweb.settings import Settings
from fsweb.state import AppState, build_state

log = logging.getLogger("fsweb.worker")


def run_once(state: AppState, queues: list[str], worker_id: str = "test-worker") -> bool:
    """Claim and process at most one job. Returns False when the queue is empty."""
    job = claim(state.db, queues, worker_id)
    if job is None:
        return False
    process(state, job)
    return True


def drain(state: AppState, queues: list[str], *, max_jobs: int = 100) -> int:
    n = 0
    while n < max_jobs and run_once(state, queues):
        n += 1
    return n


def component_for(queues: list[str]) -> str:
    if "ai-gateway" in queues and queues != ["ai-gateway"]:
        raise ValueError("the ai-gateway queue must run in its own process (the only component with egress)")
    return "ai-gateway" if queues == ["ai-gateway"] else "worker"


def run_forever(
    stop_event: threading.Event,
    queues: list[str] | None = None,
    *,
    state: AppState | None = None,
    poll: float = 1.0,
    worker_id: str | None = None,
    once: bool = False,
) -> None:
    """Process jobs until ``stop_event`` is set. Installs no signal handlers (safe in a thread)."""
    queues = list(queues or ["default"])
    st = state or build_state(Settings.from_env(), component=component_for(queues))
    wid = worker_id or f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"
    log.info("worker started", extra={"queues": queues, "worker_id": wid, "component": st.component})
    last_reap = 0.0
    while not stop_event.is_set():
        if time.monotonic() - last_reap > 60:
            try:
                reap_stale(st.db, timedelta(minutes=15))
            except Exception:
                log.exception("reaper failed")
            last_reap = time.monotonic()
        try:
            worked = run_once(st, queues, wid)
        except Exception:
            log.exception("job processing failed")
            worked = False
        if not worked:
            if once:
                break
            stop_event.wait(poll)
    log.info("worker stopped", extra={"worker_id": wid})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fsweb.worker")
    ap.add_argument("--queues", default=os.environ.get("FSWEB_QUEUES", "default"), help="comma-separated: default | ai-gateway")
    ap.add_argument("--once", action="store_true", help="process the queue until empty, then exit")
    ap.add_argument("--poll", type=float, default=1.0)
    ap.add_argument("--worker-id", default=f"{socket.gethostname()}:{os.getpid()}")
    ap.add_argument("--metrics-port", type=int, default=int(os.environ.get("FSWEB_WORKER_METRICS_PORT", "0") or 0),
                    help="expose fsweb_run_duration_seconds etc. on this port (0 = off)")
    args = ap.parse_args(argv)
    queues = [q.strip() for q in args.queues.split(",") if q.strip()]
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    try:
        component = component_for(queues)
    except ValueError as exc:
        log.error(str(exc))
        return 2
    if args.metrics_port:
        from prometheus_client import start_http_server

        from fsweb.metrics import REGISTRY

        start_http_server(args.metrics_port, registry=REGISTRY)
    state = build_state(settings, component=component)
    stop = threading.Event()

    def _stop(*_: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    run_forever(stop, queues, state=state, poll=args.poll, worker_id=args.worker_id, once=args.once)
    state.db.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
