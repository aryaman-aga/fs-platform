# Alert: WalArchiveLagHigh

**Severity:** critical · **Rule:** `ops/monitoring/alerts.yml` · **Owner:** IT on-call (E-OPS-04)

## Meaning
Completed WAL segments are not reaching the pgBackRest repository (or archive_command is failing).

## Impact
Point-in-time recovery would lose recent transactions (RPO 15 min); `pg_wal` grows until the disk fills and PostgreSQL stops.

## Diagnose
1. `SELECT * FROM pg_stat_archiver;` — failed_count, last_failed_wal, last_failed_time.
2. `docker compose --env-file /opt/fs-platform/ops/.env -f /opt/fs-platform/ops/docker-compose.yml logs --tail=200 postgres | grep -i archive`.
3. `docker compose --env-file /opt/fs-platform/ops/.env -f /opt/fs-platform/ops/docker-compose.yml exec -u postgres postgres pgbackrest --stanza=fs check`.
4. Free space on the `pgbackrest-repo` volume and on `pgdata`.

## Remediate
- Repository full → free space or extend the volume; the backlog archives automatically.
- Cipher pass / config mismatch → correct `PGBACKREST_CIPHER_PASS` (never re-generate it: older backups need the original).
- After recovery take a fresh full backup: `ops/backup/pgbackrest-backup.sh full`.

## Escalate
Critical: if pg_wal exceeds 70 % of its volume, page IT immediately — a full disk stops the database.

## Record
- Open an incident ticket (time detected, actions, root cause, follow-ups) and link it in the monthly operations report (ops/itgc/monthly-ops-report-template.md).
- Never paste client names, figures or workbook content into tickets, chat or alerts — use engagement / run IDs.
