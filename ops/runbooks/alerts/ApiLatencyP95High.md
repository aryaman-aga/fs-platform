# Alert: ApiLatencyP95High

**Severity:** warning · **Rule:** `ops/monitoring/alerts.yml` · **Owner:** IT on-call (E-OPS-04)

## Meaning
The 95th percentile API latency (excluding SSE streams) is above the 300 ms SLO for 15 minutes.

## Impact
Slow screens; at worst request time-outs at the Caddy edge.

## Diagnose
1. Grafana → *FS-Platform — overview* → *API latency p95 by route*: is it one route or all?
2. Tempo: open a slow trace (Explore → Tempo, filter service `fsweb`, duration > 1 s) and see which span is slow (DB, storage, engine call).
3. PostgreSQL: `SELECT pid, now()-query_start AS age, state, wait_event FROM pg_stat_activity WHERE state <> 'idle' ORDER BY age DESC LIMIT 10;` (statement text is not logged by design).
4. Host: CPU steal, memory pressure, disk latency (node_exporter panels).

## Remediate
- One route: open an incident ticket for the developer; check whether a recent release changed it (release log).
- DB-bound: check autovacuum and long transactions; consider `VACUUM (ANALYZE)` on the hot tables in the maintenance window.
- Capacity: add an API replica (HA overlay / Helm `api.replicas`) and record the capacity review (E-ITGC-04).

## Escalate
Raise to the developer (L2) if latency stays above SLO for a full business day.

## Record
- Open an incident ticket (time detected, actions, root cause, follow-ups) and link it in the monthly operations report (ops/itgc/monthly-ops-report-template.md).
- Never paste client names, figures or workbook content into tickets, chat or alerts — use engagement / run IDs.
