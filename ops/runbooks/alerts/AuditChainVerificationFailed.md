# Alert: AuditChainVerificationFailed

**Severity:** critical · **Rule:** `ops/monitoring/alerts.yml` · **Owner:** IT on-call (E-OPS-04)

## Meaning
The nightly verification of the hash-chained audit trail failed, or did not run for 26 hours.

## Impact
Possible tampering with `audit_event`, or loss of the tamper-evidence control (06 §4). Treat as a potential security incident until disproved.

## Diagnose
1. `docker compose --env-file /opt/fs-platform/ops/.env -f /opt/fs-platform/ops/docker-compose.yml exec api python -m fsweb.manage verify-audit-chain` — which event ID breaks the chain?
2. Compare with the write-once head files (`/audit-heads`) and the head hashes e-mailed to the Firm Admin each night.
3. PostgreSQL logs for DDL or superuser sessions around the break (DDL is logged; DML is not).
4. If the job did not run: `journalctl -u fs-audit-chain.service`.

## Remediate
- Do NOT modify or 'repair' audit rows. Preserve evidence: take a pgBackRest backup and export the DB logs.
- If tampering is plausible, start the incident-response runbook (ops/runbooks/incident-response.md) — this may be CERT-In reportable (6-hour clock).
- Restore the last verified state into quarantine for comparison (restore-full.md, point-in-time before the break).

## Escalate
Immediately to the Firm Admin and the CERT-In point of contact.

## Record
- Open an incident ticket (time detected, actions, root cause, follow-ups) and link it in the monthly operations report (ops/itgc/monthly-ops-report-template.md).
- Never paste client names, figures or workbook content into tickets, chat or alerts — use engagement / run IDs.
