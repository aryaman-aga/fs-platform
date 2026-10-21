# Alert: OffsiteBackupStale

**Severity:** critical · **Rule:** `ops/monitoring/alerts.yml` · **Owner:** IT on-call (E-OPS-04)

## Meaning
The newest restic snapshot in the off-site, object-locked bucket is older than 26 hours.

## Impact
The immutable copy (ransomware protection) is not current; site loss would lose more than 24 h of objects.

## Diagnose
1. `journalctl -u fs-restic-offsite.service -n 200`.
2. Internet / egress for the `backup` container; cloud credentials expiry; bucket policy or object-lock changes.
3. Was BackupJobFailed{kind="restic_offsite"} firing too (it inhibits this alert)?

## Remediate
- Run `systemctl start fs-restic-offsite.service` after fixing access; confirm `fs_backup_snapshot_last_timestamp_seconds{target="offsite"}` updates.
- Rotate expired credentials through the change process (E-ITGC-01).

## Escalate
Firm Admin after 48 hours without an off-site copy; consider an interim encrypted USB copy taken off-site (07 §3 option).

## Record
- Open an incident ticket (time detected, actions, root cause, follow-ups) and link it in the monthly operations report (ops/itgc/monthly-ops-report-template.md).
- Never paste client names, figures or workbook content into tickets, chat or alerts — use engagement / run IDs.
