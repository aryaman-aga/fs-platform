# Alert: DiskUsageHigh

**Severity:** warning · **Rule:** `ops/monitoring/alerts.yml` · **Owner:** IT on-call (E-OPS-04)

## Meaning
A filesystem is more than 80 % full.

## Impact
At 100 % PostgreSQL stops, WAL archiving fails and uploads are rejected.

## Diagnose
1. `df -h` and `du -xh --max-depth=2 /var/lib/docker | sort -h | tail` on the host.
2. Docker volumes: pgdata, pgbackrest-repo, minio-data, loki-data, prometheus-data.
3. Is WAL piling up (WalArchiveLagHigh)? Are old images/build caches present (`docker system df`)?

## Remediate
- Remove unused images/build cache: `docker image prune -a` (never prune volumes).
- Extend the disk/volume (capacity review, E-ITGC-04) — storage grows ~200 GB per year (04 §9).
- Never delete engagement objects or backups to make space: retention is a legal requirement (ADR-0009).

## Escalate
IT (L1); at 90 % treat as urgent.

## Record
- Open an incident ticket (time detected, actions, root cause, follow-ups) and link it in the monthly operations report (ops/itgc/monthly-ops-report-template.md).
- Never paste client names, figures or workbook content into tickets, chat or alerts — use engagement / run IDs.
