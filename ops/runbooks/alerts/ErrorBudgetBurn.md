# Alert: ErrorBudgetBurn

**Severity:** critical · **Rule:** `ops/monitoring/alerts.yml` · **Owner:** IT on-call (E-OPS-04)

## Meaning
5xx responses are consuming the 0.5 % monthly error budget at 14.4× (fast) or 6× (slow) the sustainable rate.

## Impact
Users see errors; if it continues the availability SLO (99.5 %) is breached this month.

## Diagnose
1. Grafana *Request rate by status* and Loki: `{site="fs-primary"} |= "\"status\": 5"` — which route and error code? Error bodies carry a correlation ID; jump to the trace.
2. Correlate with the release log: did a deployment happen in the last hour?
3. Check dependencies: database, object storage, disk space, render pool.

## Remediate
- Release-related: roll back to the previous version (ops/itgc/change-management.md §5).
- Dependency-related: fix the dependency (see its runbook) — API errors will stop by themselves.
- Freeze non-emergency releases until the burn stops (E-OPS-01: error budgets drive release pace).

## Escalate
Critical during filing season: page the developer immediately; record an incident with RCA (E-ITGC-04).

## Record
- Open an incident ticket (time detected, actions, root cause, follow-ups) and link it in the monthly operations report (ops/itgc/monthly-ops-report-template.md).
- Never paste client names, figures or workbook content into tickets, chat or alerts — use engagement / run IDs.
