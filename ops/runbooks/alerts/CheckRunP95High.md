# Alert: CheckRunP95High

**Severity:** warning · **Rule:** `ops/monitoring/alerts.yml` · **Owner:** IT on-call (E-OPS-04)

## Meaning
Workbook check runs take longer than the 60 s p95 target over the last hour.

## Impact
Preparers wait longer for findings; queue backlog may follow.

## Diagnose
1. Grafana *Run duration p95 by kind* and *Job queue depth*.
2. Are unusually large workbooks being checked (size / cells in the run record — never the content)?
3. `docker compose --env-file /opt/fs-platform/ops/.env -f /opt/fs-platform/ops/docker-compose.yml top worker` — CPU / memory per worker; `docker stats`.
4. Was the engine or rule set upgraded recently (run records show engine_version and rule_set_version)?

## Remediate
- Add worker replicas (`FS_WORKER_REPLICAS`, Helm `worker.replicas`) if CPU-bound.
- If a specific rule regressed, open a rule-change incident; the regression suite (E-ITGC-02) must be re-run for the fix.

## Escalate
Developer (L2) if a rule or engine release caused the regression.

## Record
- Open an incident ticket (time detected, actions, root cause, follow-ups) and link it in the monthly operations report (ops/itgc/monthly-ops-report-template.md).
- Never paste client names, figures or workbook content into tickets, chat or alerts — use engagement / run IDs.
