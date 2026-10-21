# Control matrix — draft mapping to frameworks (E-ITGC-06, 12 §5)

Maps the engagement controls (06 §2) and the platform's IT general controls to ISO/IEC 27001:2022 Annex A,
SOC 2 Trust Services Criteria and SQC 1 / SQM 1. Code and test links are completed in
`docs/CONTROL_MATRIX.md` (maintained with the traceability matrix).

## Engagement controls

| # | Control | Type | ISO 27001:2022 Annex A | SOC 2 TSC | SQC 1 / SQM 1 |
|---|---|---|---|---|---|
| C1 | Source files immutable | Preventive | 8.13 backup, 5.33 protection of records, 8.32 change management | PI1.4, PI1.5, CC8.1 | engagement documentation — integrity of working papers |
| C2 | Maker-checker on fix plans | Preventive | 5.3 segregation of duties, 8.32 | CC5.1, CC8.1, PI1.3 | engagement performance — review responsibilities |
| C3 | Balancing limited to rounding (≤ 0.20) | Preventive | 8.32 | PI1.3 | engagement performance — quality of work |
| C4 | No typed plugs introduced | Detective | 8.29 security testing in development and acceptance | PI1.3 | engagement performance |
| C5 | Post-write verification | Detective | 8.29, 8.32 | PI1.3, PI1.4 | engagement performance |
| C6 | Narrative as typed text | Preventive | 8.32 | PI1.2, PI1.3 | engagement performance |
| C7 | PDF matches workbook | Detective | 8.29 | PI1.4 | engagement documentation — final file assembly |
| C8 | Signatory master (DIN / names / dates) | Detective | 5.33 | PI1.3 | engagement performance |
| C9 | AI egress approval | Preventive | 5.14 information transfer, 8.12 data leakage prevention, 5.23 cloud services | C1.1, CC6.7, CC9.2 | relevant ethical requirements — confidentiality; resources — technological resources |
| C10 | Segregation of duties (RBAC) | Preventive | 5.3, 5.15 access control, 5.18 access rights, 8.2 privileged access | CC6.1, CC6.3 | governance and leadership; resources |
| C11 | Period scope | Detective | 8.29 | PI1.3 | engagement performance |
| C12 | Change after release (diff) | Detective | 8.32 | CC8.1, PI1.4 | engagement performance |
| C13 | Backups and restore drills | Corrective | 8.13 information backup, 8.14 redundancy, 5.30 ICT readiness for business continuity | A1.2, A1.3 | resources — technological resources |
| C14 | Access review (quarterly) | Detective | 5.18 access rights, 5.16 identity management | CC6.2, CC6.3 | monitoring and remediation |

## IT general controls (E-ITGC)

| ID | Control | ISO 27001:2022 Annex A | SOC 2 TSC | SQC 1 / SQM 1 |
|---|---|---|---|---|
| E-ITGC-01 | Change management (ticket, approver ≠ developer, emergency review 48 h) | 8.32 change management, 8.25 secure development life cycle | CC8.1 | resources — technological resources (implementation and maintenance) |
| E-ITGC-02 | Rule-change regression and approval | 8.29, 8.32, 8.33 test information | CC8.1, PI1.3 | resources (intellectual resources: methodology, tools) |
| E-ITGC-03 | Access management (joiner/mover/leaver) | 5.15, 5.16, 5.18, 8.2 | CC6.1, CC6.2, CC6.3 | resources; monitoring |
| E-ITGC-04 | Operations (monitoring, backups, drills, incidents) | 8.15 logging, 8.16 monitoring, 8.13, 5.24–5.28 incident management, 8.17 clock synchronisation | CC7.1–CC7.5, A1.1–A1.3 | monitoring and remediation |
| E-ITGC-05 | Environment segregation (T2) | 8.31 separation of development, test and production environments, 8.33 | CC8.1 | resources |
| E-ITGC-06 | Control matrix and quarterly evidence pack (T2) | 5.35 independent review of information security, 5.36 compliance with policies | CC4.1, CC4.2 | monitoring and remediation |

## Other platform controls (selected)

| Area | Requirement | ISO 27001:2022 Annex A | SOC 2 TSC |
|---|---|---|---|
| Authentication and MFA | 05 §1, E-SEC-01/03 | 5.17 authentication information, 8.5 secure authentication | CC6.1 |
| Encryption and keys | 05 §4, E-SEC-07, E-BCP-02 | 8.24 use of cryptography | CC6.1, C1.1 |
| Audit trail (hash chain) | 06 §4 | 8.15 logging | CC7.2, PI1.4 |
| CERT-In incident reporting and logs | E-SEC-05 | 5.24–5.26, 5.31 legal and regulatory requirements, 8.15, 8.17 | CC7.3, CC7.4 |
| DPDP | E-SEC-06 | 5.34 privacy and protection of PII, 5.31 | C1.1, C1.2 (privacy criteria if in scope) |
| Malware / upload scanning | 05 §5 | 8.7 protection against malware | CC6.8 |
| Vulnerability management | E-SEC-08/12 | 8.8 management of technical vulnerabilities | CC7.1 |
| Network segregation and egress | 04 §6, ADR-0020 | 8.20 network security, 8.22 segregation of networks | CC6.6 |
| Data leakage controls | E-SEC-10 | 8.12 data leakage prevention | C1.1 |
| Business continuity | E-BCP-01…04 | 5.29 information security during disruption, 5.30 | A1.2, A1.3 |

Framework references are to control themes and criteria, not to paragraph numbers of the standards;
confirm the final mapping with the firm's ISO 27001 / SOC 2 advisor before an external assessment.
