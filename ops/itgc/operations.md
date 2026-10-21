# Operations procedure (E-ITGC-04, E-ITGC-05, E-OPS-04)

## 1. Daily (IT on-call, 10 minutes)

- Alerts reviewed; every fired alert has a ticket (runbooks in `ops/runbooks/alerts/`).
- Backup dashboard: all `fs_backup_last_run_status` = 1, off-site snapshot < 26 h, WAL pending = 0.
- Audit-chain verification passed last night (head e-mail received by the Firm Admin).

## 2. Weekly

- `restic check` results; disk growth trend; job failures by rule (rule-quality signal, E-RULE-05).
- Render workers: Excel build unchanged unless a change ticket exists.

## 3. Monthly

- Restore drill (control C13) evidence filed; key-recovery drill twice a year.
- Capacity review: CPU, memory, disk, queue depth at peak, storage growth vs plan (≈ 200 GB / year).
- Patch window: OS, containers (rebuild on new base digests), Excel build; vulnerability SLAs
  (critical 7 days, high 30 days).
- Release-log reconciliation (change-management.md §3) and access sample test (access-management.md).
- Monthly operations report (template in this folder) to the Firm Admin and partners.

## 4. Incidents

Every incident: ticket with timeline, impact, root cause, actions; CERT-In / DPDP decisions recorded
(ops/runbooks/incident-response.md). S1/S2: RCA within 10 business days.

## 5. Environment segregation (E-ITGC-05, T2)

| Environment | Data | Who has access |
|---|---|---|
| dev (developer PCs, CI) | synthetic and anonymised fixtures only | developers |
| staging | anonymised fixtures only | developers, IT, testers |
| prod | client data | firm users per RBAC; IT for operations without client-data access (break-glass only) |

Production data is never copied to dev or staging. Real-file fixtures exist only on the firm's own
machine (`fixtures/private/`, git-ignored) for rule regression runs.
