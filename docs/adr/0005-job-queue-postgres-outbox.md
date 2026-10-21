# ADR-0005: Job queue: PostgreSQL transactional outbox with SKIP LOCKED; Redis optional

- **Status:** Accepted
- **Date:** 2026-10-21
- **Deciders:** lead architect (autonomous build); to be ratified by the partners
- **Requirements:** 04 §3, E-ARCH-02, E-ARCH-05, D2

## Context

04 lists Redis + RQ/Celery. E-ARCH-05 requires idempotent jobs and atomic DB-change + queue-message commits; E-ARCH-02 requires stateless nodes. Fewer moving parts suit a small firm ("prefer boring technology").

## Decision

The **job row is the outbox message**: a run and its job are inserted in one transaction; workers claim jobs with `SELECT … FOR UPDATE SKIP LOCKED` (safe fallback on SQLite for Mode C); every job carries an **idempotency key** so a duplicate submission returns the existing run. **Redis is optional** — used in the HA profile for shared rate limits and short-lived locks.

## Consequences

- Good: Exactly-once enqueue semantics by construction; no dual-write problem.
- Good: One less stateful service to back up and secure in the single-node profile.
- Bad / cost: Polling latency of a few hundred milliseconds; acceptable for jobs of seconds to minutes.
- Bad / cost: Very high job rates would need a broker; far beyond the 12 §4 scale.
