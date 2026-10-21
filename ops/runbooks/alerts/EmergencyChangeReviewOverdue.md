# Alert: EmergencyChangeReviewOverdue

**Severity:** warning · **Rule:** `ops/monitoring/alerts.yml` · **Owner:** Firm Admin / change approver (E-ITGC-01)

## Meaning
An emergency change was deployed more than 48 hours ago and nobody other than its developer has approved it
yet. Emergency changes may be deployed before approval, but the after-the-fact review is part of the control.

## Impact
The change log shows an unreviewed production change; the quarterly evidence pack (`releases.csv`, column
`review_overdue`) records the exception for the auditors.

## Diagnose
1. *Admin → Operations → Releases* (or `GET /api/v1/admin/releases`): the emergency release with
   `review_overdue: true`, its ticket (REL-YYYY-NNN), developer and deployment time.
2. Read the ticket: what changed, why it was an emergency, the test evidence and the rollback plan.

## Remediate
- An approver who is not the developer reviews the change and approves it in *Releases* (the API refuses the
  developer and records the attempt).
- If the change should not stay, follow its rollback plan and record a new release.

## Escalate
The engagement partner responsible for IT controls if no independent approver is available within one
working day.

## Record
- Note the late review and its reason in the monthly operations report (ops/itgc/monthly-ops-report-template.md).
- Never paste client names, figures or workbook content into tickets, chat or alerts — use release and run IDs.
