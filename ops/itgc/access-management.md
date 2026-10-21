# Access management — joiner / mover / leaver (E-ITGC-03, E-SEC-04, control C14)

| Event | Who acts | Within | Platform action |
|---|---|---|---|
| Joiner (firm) | Firm Admin on HR request | before day 1 | invite (single-use link, 72 h); role per 05 §2; MFA enrolment at first login; SSO account in the IdP |
| Joiner (client user) | engagement Partner / Manager | on request | invite linked to exactly one client; least privilege (Client Uploader / Viewer) |
| Mover | Firm Admin | 1 business day | change role / client assignments; old assignments removed, not left in place |
| Leaver | Firm Admin (triggered by HR / IdP) | **1 business day** | disable user → sessions and tokens revoked immediately; history kept; SSO disabled in the IdP (≤ 5 min effect, E-SEC-01) |
| Privileged access | second admin approves | time-boxed | just-in-time elevation with step-up MFA and reason; break-glass sealed and alerted (E-SEC-03) |

## Quarterly access recertification (C14, E-SEC-04)

1. The campaign opens on the first day of each quarter (`fs-access-review-start.timer` on the single-node
   profile, the `start-access-review` CronJob on Kubernetes; `python -m fsweb.manage start-access-review`
   runs it by hand). A Firm Admin can also start it in *Admin → Access reviews*;
   `python -m fsweb.manage access-review-report` prints the users × clients × roles list.
2. Each Manager / Partner certifies or revokes every assignment for their clients.
3. Items not answered within **14 days are revoked automatically** (daily `auto-revoke` job); the last
   Firm Admin is escalated instead of revoked.
4. The campaign report (decisions, who decided, when) is stored as evidence and included in the quarterly
   evidence pack.

## Sample test (operations report)

Each month: pick five leavers / movers from HR records and confirm the platform change happened within
1 business day (user status, revoked sessions, assignments). Record exceptions.
