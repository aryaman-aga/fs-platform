# Digital Personal Data Protection (E-SEC-06) — draft for counsel review

Applies the Digital Personal Data Protection Act, 2023 and the DPDP Rules as notified (track the
commencement date of each rule). The firm is the **Data Fiduciary** for its users and client contacts;
for personal data inside client workbooks it processes on the client's behalf under the engagement letter
(professional confidentiality under the ICAI Code of Ethics applies in addition).

**Grievance Officer / DPO contact:** _to be named by the firm_ (configurable in *Admin → Compliance
settings*; shown in the privacy notice and the footer).

## 1. Personal-data inventory

| Category | Data | Purpose | Lawful basis | Where | Retention |
|---|---|---|---|---|---|
| Firm users | name, e-mail, role, MFA secret (encrypted), login times, IP, user agent | access control, security, audit | employment / legitimate use | PostgreSQL (India) | employment + 7 years for audit trail |
| Client users | name, e-mail, client link, MFA secret, login times, IP | client portal, uploads, confirmations | consent at invitation (notice shown) | PostgreSQL (India) | engagement retention |
| Signatories | name, designation, DIN, membership number, FRN | signatory checks on statements (TXT-01, C8) | contract / legal obligation (Companies Act disclosures) | PostgreSQL, DIN field-encrypted | engagement retention |
| Client identifiers | PAN, CIN, GSTIN (companies; PAN of individuals is personal data) | master data | contract | PostgreSQL, field-encrypted | engagement retention |
| Workbook content | names in related-party notes, remuneration of KMP | the engagement itself | contract with the client | object store (India), encrypted | 7 years from report |
| Audit trail | user IDs, IPs, actions | SA 230 / SQC 1 documentation, security | legal obligation | PostgreSQL (append-only) | permanent |
| Security logs | IPs, user IDs, request metadata (no content) | CERT-In obligations, security | legal obligation | Loki (India) | 180 days |
| AI payloads | pseudonymised findings and short context | optional AI review | approval per payload; client consent per engagement letter | provider in an approved region | per provider terms; payload hash kept locally |

Aadhaar numbers are never needed; an optional DLP rule blocks uploads containing Aadhaar or PAN patterns
for the wrong client (E-SEC-10).

## 2. Notice and consent

- Client users see a privacy notice at invitation (purpose, data, retention, rights, Grievance Officer)
  and accept it before first use; the acceptance is recorded with a timestamp.
- Firm users receive the notice through HR onboarding; the AI policy is accepted separately (E-AI-01).
- Consent withdrawal by a client user ends portal access; records required by law are retained.

## 3. Data-principal requests (DSR)

Workflow in *Admin → DSR requests* (`dsr_request`): intake → identity verification → assessment →
action → response → closure, with a due date (default 30 days or the period in the Rules, whichever is
shorter).

| Request | Action | Limits |
|---|---|---|
| Access / summary | export of the principal's account data and processing summary | no other people's data; no client workbook content belonging to the client |
| Correction | update account data; signatory corrections go through the engagement team | historical audit entries are not rewritten (append a correction event) |
| Erasure | delete or pseudonymise account data that is no longer needed | **legal-retention guard**: engagement documentation (SA 230 / SQC 1, 7 years) and the audit trail are retained; the response explains what was retained and why |
| Grievance | route to the Grievance Officer | escalation to the Data Protection Board is the principal's right |

## 4. Personal-data breach

Follow `ops/runbooks/incident-response.md` §3: inform the Data Protection Board of India and affected Data
Principals without delay in the prescribed form, and send the detailed report within the period the Rules
prescribe (72 hours in the notified Rules — confirm with counsel). CERT-In reporting (6 hours) runs in
parallel when the incident is also a cyber-security incident.

## 5. Processors and transfers

Hosting provider (India region), backup storage (India region), optional AI provider (approved region,
contracted retention; pseudonymised payloads only), e-mail relay. Keep a register of processors with
contract references; no transfers to countries restricted by notification under the Act.

## 6. Processing records

The audit trail, the DSR register, the breach register (`incident`) and this inventory form the records
of processing. Review yearly and on every new feature that touches personal data (threat model update).
