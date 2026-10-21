# Incident response (E-SEC-05 CERT-In, E-SEC-06 DPDP, 05 §4)

**Roles** (named by the firm in *Admin → Compliance settings*; until then the Firm Admin holds all):
Incident commander · CERT-In point of contact · DPDP Grievance Officer / breach lead · IT lead · Partner liaison.

## 1. Detect and triage (start the clock)

Sources: alerts (`AuditChainVerificationFailed`, repeated permission denials, break-glass use, admin
elevation), user reports, the hosting provider, CERT-In advisories.

1. Open an incident in *Admin → Incidents* (`incident` table): detected_at (IST), reporter, systems,
   severity (S1 critical … S4 low). The platform computes the **6-hour CERT-In deadline** from detected_at.
2. Decide within **1 hour** whether the incident is CERT-In reportable. Types listed in the CERT-In
   Directions (28 April 2022) include, among others: targeted scanning/probing of critical systems;
   compromise of critical systems or information; unauthorised access to IT systems or data; website
   defacement or intrusion; malicious code attacks (virus, ransomware, worms, trojans); attacks on
   servers (database, mail, DNS); identity theft, spoofing and phishing; data breach; data leak; attacks
   on cloud and on applications. When in doubt, report.
3. Preserve evidence before changing anything: snapshot volumes (`pgbackrest backup --type=incr`),
   export logs from Loki for the window, copy the audit-chain head files, note container IDs and
   image digests. Logs are retained 180 days in India (Loki retention) and the audit chain is permanent.

## 2. Report to CERT-In within 6 hours of noticing

- Use the incident reporting form / e-mail published on the CERT-In website (incident@cert-in.org.in at
  the time of writing — confirm the current channel on cert-in.org.in) and attach the requested details:
  time of occurrence and detection, type, affected systems, symptoms, actions taken.
- Record `reported_at` and the CERT-In acknowledgement in the incident. Provide logs and further
  information to CERT-In on request (the 180-day retention exists for this).
- Follow-up reports as the investigation progresses.

## 3. Personal data breach (DPDP Act 2023 and Rules)

If personal data (users, client contacts, signatories, identifiers) may be affected:

- Inform the Data Protection Board of India and each affected Data Principal **without delay**, in the
  form and with the details prescribed by the DPDP Rules, and send the detailed report within the period
  the Rules prescribe (72 hours in the notified Rules — confirm with counsel for the current text).
- Client confidentiality (ICAI Code of Ethics): the engagement Partner informs affected clients.
- Track in the incident and in `dsr_request`/breach records (docs/security/dpdp.md).

## 4. Contain, eradicate, recover

- Contain: disable affected accounts (revokes sessions and tokens immediately), rotate the worker token,
  block IPs at Caddy/Cloudflare, take the AI gateway offline (`FSWEB_AI_ENABLED=false`) if egress is
  implicated, isolate hosts at the network level (do not power off before memory capture if forensics
  is needed).
- Eradicate: patch, rebuild from images and IaC (never "clean" a compromised host), rotate secrets per
  docs/security/key-management.md.
- Recover: restore-full.md (from the object-locked off-site copy if integrity is in doubt); verify the
  audit chain; monitor closely for 14 days.

## 5. Close

Root-cause analysis within 10 business days (`incident.rca`), lessons learned, control changes through
change management (E-ITGC-01), tabletop scenario updated (ops/tabletop-exercises.md). S1/S2 incidents
are summarised in the quarterly evidence pack.

## Contacts (fill in; keep the printed copy with the BCP)

| Role | Name | Phone | E-mail |
|---|---|---|---|
| Incident commander | | | |
| CERT-In point of contact | | | |
| DPDP Grievance Officer | | | |
| IT lead / hosting provider | | | |
| Forensics retainer (optional) | | | |
