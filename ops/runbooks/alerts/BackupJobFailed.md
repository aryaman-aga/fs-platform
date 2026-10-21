# Alert: BackupJobFailed

**Severity:** critical · **Rule:** `ops/monitoring/alerts.yml` · **Owner:** IT on-call (E-OPS-04)

## Meaning
The last run of a backup job (`kind` label: pgbackrest_full/diff, restic_nas, restic_offsite, restic_forget_*, restic_check_*, restore_drill) reported failure.

## Impact
RPO at risk: the affected copy is not current. Repeated failures break 3-2-1-1-0 (07 §1).

## Diagnose
1. `journalctl -u fs-<job>.service -n 200` (or `/var/log/fs-backup.log` with cron).
2. pgBackRest: `docker compose --env-file /opt/fs-platform/ops/.env -f /opt/fs-platform/ops/docker-compose.yml exec -u postgres postgres pgbackrest --stanza=fs info` and `... check`.
3. restic: credentials (`ops/backup/restic.env`), NAS reachability (`ssh fsbackup@nas...`), off-site bucket access and object-lock errors.
4. Disk space on the host and the NAS (DiskUsageHigh).

## Remediate
- Fix the cause and re-run the job by hand: `systemctl start fs-<job>.service`; confirm the metric returns to 1.
- restic lock left by a crash: `restic unlock` (only after confirming no other restic process is running).
- pgBackRest stanza mismatch after a restore: `pgbackrest --stanza=fs stanza-upgrade`.

## Escalate
If no successful backup for 24 hours, inform the Firm Admin and record an incident (E-ITGC-04).

## Record
- Open an incident ticket (time detected, actions, root cause, follow-ups) and link it in the monthly operations report (ops/itgc/monthly-ops-report-template.md).
- Never paste client names, figures or workbook content into tickets, chat or alerts — use engagement / run IDs.
