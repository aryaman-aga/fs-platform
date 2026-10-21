# Firm Admin

**You manage** users, roles, clients, engagement assignments, policies, AI settings and compliance workflows.
You have no automatic access to client data beyond what your role grants; administrative actions require a
fresh authenticator code (step-up) and a reason.

## Users and access
- **Invite** firm users (or rely on SSO) and client users (linked to exactly one client). Disable leavers the
  same day — sessions and tokens are revoked immediately.
- **Roles and permission matrix** follow 05 §3; changes are audited.
- **Access reviews** (quarterly, C14): start the campaign; managers certify or revoke; unanswered items are revoked
  after 14 days; the report is stored as evidence.
- **Just-in-time elevation** requests from other admins need your approval; break-glass use raises an alert.

## Compliance
- **Compliance settings:** CERT-In point of contact, DPDP Grievance Officer, retention period (≥ 7 years), legal holds.
- **Incidents:** record detection time; the platform shows the 6-hour CERT-In deadline (ops/runbooks/incident-response.md).
- **DSR requests** (DPDP): access, correction, erasure — the legal-retention guard explains what must be kept.
- **AI policy:** enable a provider and region only after partner approval; users accept the policy once.

## Operations
- **System health, backups and restore drills:** status of the last backups, WAL archiving, off-site copy and
  the monthly drill; the nightly audit-chain head hash is e-mailed to you — keep those e-mails.
- **Releases:** the change log (who approved which release); reconcile monthly.
- **Usage:** per-client monthly usage for billing.
- **Evidence pack:** export each quarter for partner review (E-ITGC-06).
