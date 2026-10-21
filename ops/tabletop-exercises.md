# Tabletop exercises (E-BCP-04) — twice a year

Each exercise takes 90 minutes with the incident commander, IT lead, a partner, the CERT-In contact and
the DPDP Grievance Officer. A facilitator injects events at the times shown; the team talks through
decisions using the real runbooks. Record decisions, gaps and actions (owner, due date) in the exercise
record; actions go through change management.

## Scenario A — ransomware on the office server (filing season)

| T+ | Inject | Expected decisions |
|---|---|---|
| 0 | Users report files will not open; the API returns 500s; `ErrorBudgetBurn` fires | declare incident, start the 6-hour CERT-In clock |
| 20 min | Encrypted files found on the host; ransom note | isolate the host, do not pay, preserve evidence |
| 45 min | NAS snapshots also encrypted | restore from the **object-locked off-site** copy; reconstruct keys with 2 partners |
| 2 h | Cloud rebuild in Central India started from IaC | communicate to users; Mode C for urgent client-site work |
| 5 h | Report to CERT-In due | confirm the report was sent; DPDP assessment of personal data |
| Close | Restored; data-loss window 12 minutes | users re-check work after the recovered point; RCA |

## Scenario B — office outage (power / access) for three days

Users work remotely through the tunnel if the server room has power; otherwise DR to the India cloud.
Check: who decides, how users are told, how render capacity is restored, what the BCP manual fallback means
for an engagement due that week.

## Scenario C — insider data leak

A former staff member's account downloaded many draft PDFs the evening before leaving. Check: leaver
process timing (E-ITGC-03), download watermarks identify the user (E-SEC-10), audit trail evidence,
DPDP / client notification decisions, access recertification gaps (E-SEC-04).

## Scenario D — cloud-region outage (when hosted in the cloud or with cloud DR)

Primary region unavailable for 8 hours. Check: failover to the secondary region, data residency (both
regions in India), DNS / tunnel switch, RPO achieved, communication.

## Exercise record

| Date | Scenario | Participants | Gaps found | Actions (owner, due) | Closed |
|---|---|---|---|---|---|
| | | | | | |
