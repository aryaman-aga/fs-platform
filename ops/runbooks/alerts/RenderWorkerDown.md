# Alert: RenderWorkerDown

**Severity:** warning · **Rule:** `ops/monitoring/alerts.yml` · **Owner:** IT on-call (E-OPS-04)

## Meaning
A registered Windows render worker has not sent a heartbeat for 5 minutes.

## Impact
Excel-based rendering capacity is reduced; with no Excel worker, renders fall back to LibreOffice and finals that require Excel wait.

## Diagnose
1. RDP/console to the render host: is the `FSRenderWorker` service running? `sc query FSRenderWorker`.
2. `fsrender-win check-config` — API URL, token file, detected Excel build.
3. Network: can the host reach `https://<internal API>/api/v1/health`? Firewall / VPN changes?
4. Logs: `C:\ProgramData\FSRender\logs\fsrender.log` (job IDs and error types only).

## Remediate
- Start the service; if it crashes, run `fsrender-win once` interactively as the service account to see the error.
- Excel licence / activation problems: re-activate shared-computer activation for the service account.
- Rebuild the worker: ops/runbooks/restore-render-worker.md. Decommissioned workers must be removed from the registry so the alert clears.

## Escalate
IT (L1) for the Windows server; Microsoft licensing questions to the firm's licensing partner.

## Record
- Open an incident ticket (time detected, actions, root cause, follow-ups) and link it in the monthly operations report (ops/itgc/monthly-ops-report-template.md).
- Never paste client names, figures or workbook content into tickets, chat or alerts — use engagement / run IDs.
