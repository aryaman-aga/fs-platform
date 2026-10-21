"""DB-backed job queue implementing the transactional outbox (E-ARCH-05).

A job row is inserted in the *same* transaction as the run (and whatever state
change requested it), so a run can never exist without its job or vice versa.
Workers claim with ``FOR UPDATE SKIP LOCKED`` on PostgreSQL; on SQLite every
transaction is ``BEGIN IMMEDIATE`` so claims are serialised.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fsweb.db import SYSTEM, Database, RLSContext, utcnow
from fsweb.models import Job, Run, RunEvent

QUEUES = ("default", "render-win", "ai-gateway")
TERMINAL = {"succeeded", "failed"}


def enqueue(session: Session, run: Run, *, queue: str = "default", max_attempts: int = 3) -> Job:
    if queue not in QUEUES:
        raise ValueError(f"unknown queue {queue}")
    job = Job(
        tenant_id=run.tenant_id,
        client_id=run.client_id,
        run_id=run.id,
        queue=queue,
        status="queued",
        max_attempts=max_attempts,
        available_at=utcnow(),
        correlation_id=run.correlation_id,
        created_by=run.created_by,
    )
    session.add(job)
    session.flush()
    return job


def claim(db: Database, queues: list[str], worker_id: str) -> Job | None:
    with db.scope(SYSTEM) as s:
        stmt = (
            select(Job)
            .where(Job.status == "queued", Job.queue.in_(queues), Job.available_at <= utcnow())
            .order_by(Job.created_at)
            .limit(1)
        )
        if db.is_postgres:
            stmt = stmt.with_for_update(skip_locked=True)
        job = s.execute(stmt).scalars().first()
        if job is None:
            return None
        job.status = "claimed"
        job.worker_id = worker_id
        job.claimed_at = utcnow()
        job.heartbeat_at = utcnow()
        job.attempts += 1
        s.flush()
        s.expunge(job)
        return job


def finish(db: Database, job_id: str, *, ok: bool, error: str | None = None, retryable: bool = True) -> str:
    """Mark a job done; failed jobs are retried with exponential backoff until max_attempts."""
    with db.scope(SYSTEM) as s:
        job = s.get(Job, job_id)
        if job is None:
            return "missing"
        if ok:
            job.status = "succeeded"
            job.error = None
        elif retryable and job.attempts < job.max_attempts:
            job.status = "queued"
            job.error = (error or "")[:2000]
            job.available_at = utcnow() + timedelta(seconds=min(300, 5 * 2 ** job.attempts))
            job.worker_id = None
        else:
            job.status = "dead" if retryable else "failed"
            job.error = (error or "")[:2000]
        return job.status


def reap_stale(db: Database, stale_after: timedelta = timedelta(minutes=15)) -> int:
    """Requeue jobs whose worker stopped heart-beating (crashed / killed)."""
    n = 0
    with db.scope(SYSTEM) as s:
        cutoff = utcnow() - stale_after
        for job in s.execute(select(Job).where(Job.status == "claimed", Job.heartbeat_at < cutoff)).scalars():
            job.status = "queued" if job.attempts < job.max_attempts else "dead"
            job.worker_id = None
            job.error = "worker heartbeat lost"
            n += 1
    return n


def queue_depth(db: Database | Session) -> dict[str, int]:
    """Queued jobs per queue. Pass the caller's session when one is open (SQLite has one writer)."""
    stmt = select(Job.queue, func.count()).where(Job.status == "queued").group_by(Job.queue)
    if isinstance(db, Session):
        rows = db.execute(stmt).all()
    else:
        with db.scope(SYSTEM) as s:
            rows = s.execute(stmt).all()
    out = dict.fromkeys(QUEUES, 0)
    out.update({q: int(n) for q, n in rows})
    return out


def add_event(db: Database, run: Run | Any, message: str, pct: float, kind: str = "progress") -> None:
    """Progress events are committed immediately so the SSE stream sees them."""
    ctx = RLSContext(run.tenant_id, None, "system")
    with db.scope(ctx) as s:
        s.add(RunEvent(tenant_id=run.tenant_id, client_id=run.client_id, run_id=run.id, kind=kind, message=message[:300], pct=float(pct)))
