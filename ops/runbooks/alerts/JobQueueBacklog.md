# Alert: JobQueueBacklog

**Severity:** warning · **Rule:** `ops/monitoring/alerts.yml` · **Owner:** IT on-call (E-OPS-04)

## Meaning
More than 20 jobs have been waiting in one queue for 15 minutes.

## Impact
Check, fix, render or AI jobs start late; users see 'queued'.

## Diagnose
1. Grafana *Job queue depth* — which queue (`check`, `fix`, `render`, `render-win`, `ai-gateway`)?
2. `docker compose --env-file /opt/fs-platform/ops/.env -f /opt/fs-platform/ops/docker-compose.yml ps worker ai-gateway` and `docker compose --env-file /opt/fs-platform/ops/.env -f /opt/fs-platform/ops/docker-compose.yml logs --tail=200 worker` — are workers running and claiming jobs?
3. `render-win` backlog: is any Windows worker heartbeating? (RenderWorkerDown).
4. `ai-gateway` backlog: is AI enabled and the provider reachable? Budget exhausted (E-AI-04)?

## Remediate
- Restart stuck workers; jobs are idempotent and are re-claimed safely (E-ARCH-05).
- Scale workers for peak season (September–November capacity plan, E-OPS-05).

## Escalate
Developer (L2) if jobs fail repeatedly (attempts exhausted) rather than wait.

## Record
- Open an incident ticket (time detected, actions, root cause, follow-ups) and link it in the monthly operations report (ops/itgc/monthly-ops-report-template.md).
- Never paste client names, figures or workbook content into tickets, chat or alerts — use engagement / run IDs.
