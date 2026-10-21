# Alert: RestoreDrillMissed

**Severity:** warning · **Rule:** `ops/monitoring/alerts.yml` · **Owner:** IT on-call (E-OPS-04)

## Meaning
No successful restore drill in the last 35 days (control C13).

## Impact
Restore capability is unverified; the quarterly evidence pack will show a control exception.

## Diagnose
1. `journalctl -u fs-restore-drill.service -n 300` and the latest `/var/lib/fs-platform/evidence/restore-drill-*/` folder (`*.status` files).
2. Did step 1 (package round trip), step 2 (restic restores) or step 3 (database restore) fail?

## Remediate
- Fix the failing step, then run `ops/backup/restore_drill.sh --with-db` by hand.
- Attach the evidence folder (with MANIFEST.sha256) to the monthly operations report.

## Escalate
Record a control exception in the operations report if the drill cannot pass within 7 days.

## Record
- Open an incident ticket (time detected, actions, root cause, follow-ups) and link it in the monthly operations report (ops/itgc/monthly-ops-report-template.md).
- Never paste client names, figures or workbook content into tickets, chat or alerts — use engagement / run IDs.
