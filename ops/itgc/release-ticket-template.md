# Release ticket — REL-YYYY-NNN

| Field | Value |
|---|---|
| Title | |
| Type | standard / emergency / rule-set / template / infrastructure / Excel build |
| Requested by / date | |
| Developer(s) | |
| Approver (≠ developer) / date | |
| Planned window (IST) | |
| Version / image digests | |
| Requirement / control IDs | e.g. E-SEC-05, C7, RPT-09 |

## What changes and why

## Risk assessment

- Affected components: API / workers / UI / DB schema / rules / templates / infrastructure / render workers
- Data migration? yes / no — backward compatible? yes / no
- Security impact (threat model updated? docs/security/threat-model.md)
- Client-facing output impact (findings, fixes, PDFs)?

## Test evidence

- CI run URL and result:
- Golden regression report (required for rule / template changes, E-ITGC-02):
- AI evaluation report (required for prompt / model changes, E-AI-02):
- Manual checks performed:

## Rollback plan

## Post-deployment verification

- [ ] `/api/v1/ready` OK on every node
- [ ] audit chain verified (`python -m fsweb.manage verify-audit-chain`)
- [ ] smoke test (login with MFA, check run on a synthetic fixture, draft render)
- [ ] release recorded in the platform's release log

## Emergency change only

Second review within 48 hours by: _name / date_
