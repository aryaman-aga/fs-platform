# Alert: ApiDown

**Severity:** critical · **Rule:** `ops/monitoring/alerts.yml` · **Owner:** IT on-call (E-OPS-04)

## Meaning
Prometheus cannot scrape `/metrics` on an API instance for 2 minutes (or no API target exists).

## Impact
Users cannot log in, upload or approve. Running jobs continue in the workers but their results are not visible.

## Diagnose
1. `docker compose --env-file /opt/fs-platform/ops/.env -f /opt/fs-platform/ops/docker-compose.yml ps api` — container state and health.
2. `docker compose --env-file /opt/fs-platform/ops/.env -f /opt/fs-platform/ops/docker-compose.yml logs --tail=200 api` — look for start-up errors (settings validation refuses insecure prod configuration, database unreachable, migrations pending).
3. `docker compose --env-file /opt/fs-platform/ops/.env -f /opt/fs-platform/ops/docker-compose.yml exec api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/api/v1/ready').read())"` — readiness reports DB and storage status.
4. `docker compose --env-file /opt/fs-platform/ops/.env -f /opt/fs-platform/ops/docker-compose.yml ps postgres minio` — a dependency down makes `/ready` fail.

## Remediate
- If the container exited after a configuration change, roll the change back (E-ITGC-01 rollback plan) and restart: `docker compose --env-file /opt/fs-platform/ops/.env -f /opt/fs-platform/ops/docker-compose.yml up -d api`.
- If the database is down, follow WalArchiveLagHigh / DiskUsageHigh first — a full disk stops PostgreSQL.
- HA profile: confirm the other replica serves traffic (`fsweb_http_requests_total` still increasing) before restarting the failed node.

## Escalate
If not recovered in 30 minutes during business hours, invoke the BCP manual fallback (ops/bcp.md §4) and inform the Firm Admin.

## Record
- Open an incident ticket (time detected, actions, root cause, follow-ups) and link it in the monthly operations report (ops/itgc/monthly-ops-report-template.md).
- Never paste client names, figures or workbook content into tickets, chat or alerts — use engagement / run IDs.
