# Business continuity plan (E-BCP-01) — draft for partner approval

**Owner:** Firm Admin · **Approved by:** _partners (date)_ · **Review:** yearly and after every S1 incident.

## 1. Critical periods

| Period | Why it matters | Platform posture |
|---|---|---|
| April–May | year-end close, SPFS for foreign subsidiaries (quarter and year) | no non-emergency releases in the last 10 days before reporting deadlines |
| July–September | statutory audits, board meetings, AGM filings | 99.9 % business-hours target; daily backup checks |
| September–November | peak (12 §4 filing season); MCA filings | change freeze except fixes; extra render worker; on-call rota published |
| Quarter ends (Jun, Sep, Dec, Mar) | interim / SPFS packs | as above for the 10 days after quarter end |

## 2. Recovery objectives

RTO 4 hours / RPO 15 minutes for the platform; 1 hour to restore one engagement (07 §1). HA profile:
0 / 2 minutes for a single-node loss (12 §4).

## 3. Scenarios and responses

| Scenario | First response | Runbook |
|---|---|---|
| Platform down (hardware, OS, database) | restore on replacement hardware or the India cloud from IaC | ops/runbooks/restore-full.md |
| Office site unavailable (power, flood, access) | users work remotely through the tunnel; if the server itself is down → DR | ops/runbooks/dr-failover.md |
| Ransomware / intrusion | isolate, preserve evidence, CERT-In within 6 h, restore from the object-locked copy | ops/runbooks/incident-response.md |
| Internet outage at a client site | Mode C offline laptop with engagement packages | fsoffline/README.md |
| Excel render pool down | LibreOffice fallback for drafts; finals wait or a second worker is built | ops/runbooks/restore-render-worker.md |
| Key holder unavailable | 2-of-3 escrow: any two partners rebuild the keys | ops/runbooks/key-recovery-drill.md |
| Loss of the developer / vendor (L2) | source, runbooks, IaC and this repository's docs allow another engineer to operate and rebuild | docs/admin-guide.md |

## 4. Manual fallback

If the platform is unavailable during a critical period:

1. Engagement teams work on **local copies** of the latest workbook version downloaded before the outage
   (watermarked PDFs and versions are already on the server); no client files on personal devices.
2. Every change made offline is listed in the engagement's working paper (who, what, when).
3. When the platform returns, upload the edited workbook as a **new version**; the platform re-runs all
   checks and the diff shows every changed cell and printed figure (06 §5). Mode C users import their
   `.fspkg` packages (conflicts branch for Manager merge).
4. No final sign-off on a version that has not passed the platform's checks and PDF verification.

## 5. Contact tree

| Role | Primary | Backup |
|---|---|---|
| Incident commander (Firm Admin) | | |
| IT lead / hosting | | |
| Developer (L2) | | |
| Partners holding key shares (3) | | |
| CERT-In point of contact | | |
| DPDP Grievance Officer | | |

Print this page with the incident-response contacts and keep a copy off-site.

## 6. Testing

Tabletop exercises twice a year (ops/tabletop-exercises.md, E-BCP-04), restore drill monthly (C13),
key-recovery drill twice a year, DR failover twice a year once the warm site exists (E-BCP-03, T2).
