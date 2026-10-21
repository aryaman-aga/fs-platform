# Alert: ClockNotSynchronised

**Severity:** warning · **Rule:** `ops/monitoring/alerts.yml` · **Owner:** IT on-call (E-OPS-04)

## Meaning
The host clock is not synchronised or is more than 500 ms off the NIC/NPL time servers.

## Impact
Audit and security log timestamps lose their traceability required by the CERT-In Directions (E-SEC-05); session timeouts and TOTP may misbehave.

## Diagnose
1. `chronyc tracking` and `chronyc sources -v` — are samay1.nic.in / samay2.nic.in / time.nplindia.org reachable?
2. Firewall: UDP/123 outbound from the host.

## Remediate
- Restore NTP reachability; `chronyc makestep` once if the offset is large (record the step in the incident ticket).
- Windows render workers: `w32tm /config /manualpeerlist:"samay1.nic.in samay2.nic.in" /syncfromflags:manual /update`.

## Escalate
IT (L1).

## Record
- Open an incident ticket (time detected, actions, root cause, follow-ups) and link it in the monthly operations report (ops/itgc/monthly-ops-report-template.md).
- Never paste client names, figures or workbook content into tickets, chat or alerts — use engagement / run IDs.
