# Alert: RenderP95High

**Severity:** warning · **Rule:** `ops/monitoring/alerts.yml` · **Owner:** IT on-call (E-OPS-04)

## Meaning
PDF renders take longer than the 90 s p95 target over the last hour.

## Impact
Draft and final PDFs are delayed; finals may miss signing slots.

## Diagnose
1. Which engine rendered (render records show `engine` and `excel_build`)? Excel pool or LibreOffice fallback?
2. Render worker heartbeat ages (Grafana) — a worker down shifts load to the others or to LibreOffice.
3. On the Windows worker: Event Viewer → Application, and `C:\ProgramData\FSRender\logs\fsrender.log` for watchdog kills (Excel hangs).

## Remediate
- Restart a degraded render worker service (`fsrender-win service stop/start`).
- If Excel hangs repeatedly after an Office update, pin back to the previous recorded build (ops/runbooks/restore-render-worker.md).
- Add a second worker to the pool (E-ARCH-03).

## Escalate
IT (L1) for the Windows host; developer (L2) if specific workbooks consistently time out.

## Record
- Open an incident ticket (time detected, actions, root cause, follow-ups) and link it in the monthly operations report (ops/itgc/monthly-ops-report-template.md).
- Never paste client names, figures or workbook content into tickets, chat or alerts — use engagement / run IDs.
