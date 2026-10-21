# Runbooks

| Runbook | Use when | Target |
|---|---|---|
| [restore-engagement.md](restore-engagement.md) | one engagement must be recovered (deleted, corrupted, wrong version promoted) | 1 hour |
| [restore-full.md](restore-full.md) | host lost, database corrupted, ransomware | RTO 4 h, RPO 15 min |
| [restore-render-worker.md](restore-render-worker.md) | Windows render worker lost or rebuilt | same day |
| [incident-response.md](incident-response.md) | any security incident; CERT-In 6-hour reporting; DPDP breach intimation | 6 h to report |
| [key-recovery-drill.md](key-recovery-drill.md) | twice a year, or when a key holder changes (E-BCP-02) | — |
| [dr-failover.md](dr-failover.md) | site loss; warm DR site exercise (E-BCP-03, T2) | RTO 4 h |
| [alerts/](alerts/) | one runbook per Prometheus alert (E-OPS-04) | per alert |

Conventions for every runbook:

- Commands assume the single-node layout in `/opt/fs-platform` and
  `C="docker compose --env-file /opt/fs-platform/ops/.env -f /opt/fs-platform/ops/docker-compose.yml"`.
- Work in a ticket: record times (IST and UTC), commands run, results and approvals. The ticket
  is the evidence for the operations report (E-ITGC-04) and the quarterly evidence pack (E-ITGC-06).
- Never copy client workbooks, figures or names into tickets, chat or e-mail; refer to engagement,
  run and version IDs.
- Restores of client data need Firm Admin approval (05 §3 "approve restore of client data") and land
  in **quarantine** first; promotion needs Partner approval.
