# Threat model v1 (STRIDE)

**Scope:** FS-Platform Mode A (single-node and HA profiles), the Windows render pool, backups, Mode B (T2) and
Mode C. **Method:** STRIDE per component; each threat has a mitigation and the requirement or control that
implements it. Updated every phase (E-SEC-08); next review after the first penetration test.

**Assets:** client workbooks, PDFs and trial balances (confidential); findings, plans and review notes;
identifiers (PAN, CIN, DIN, membership numbers); user credentials and MFA secrets; the audit trail;
backup and KMS keys. **Trust boundaries:** internet ↔ edge; edge ↔ internal network; internal network ↔
AI provider (gateway only); firm network ↔ render pool; laptop ↔ firm server (packages only).

## Browser SPA

| ID | STRIDE | Threat | Mitigation | Requirement / control |
|---|---|---|---|---|
| T01 | Spoofing | Session theft through XSS or a malicious extension | strict CSP (no inline script), output encoding by React, HttpOnly + SameSite=Strict cookies, short idle timeouts | 05 §1, ASVS V3/V5 |
| T02 | Tampering | Forged state-changing request from another site (CSRF) | SameSite=Strict cookies + double-submit CSRF header on every unsafe method | 05 §1 |
| T03 | Information disclosure | Sensitive figures cached on a shared PC | no-store on API responses, logout clears state, downloads per role, watermarked PDFs | E-SEC-10 |
| T04 | Elevation of privilege | UI hides a button but the API allows the action | server-side permission checks on every endpoint; UI is not a control | 05 §3, C10 |

## Caddy / edge (WAF)

| ID | STRIDE | Threat | Mitigation | Requirement / control |
|---|---|---|---|---|
| T05 | Spoofing | Attacker reaches admin endpoints from the internet | office-CIDR allow-list for /api/v1/admin, step-up MFA for admin actions | 05 §1, E-SEC-03 |
| T06 | Tampering | Downgrade / man-in-the-middle | TLS 1.2+ only, HSTS 2 years, Cloudflare Tunnel (no open ports) | 05 §4 |
| T07 | Denial of service | Credential stuffing or request floods | rate limits per client IP (auth 20/min, API 600/min), lockout after 5 failures, Cloudflare DDoS protection | 05 §1 |
| T08 | Information disclosure | Metrics or internal render API exposed | /metrics returns 404 at the edge; /api/v1/internal/* only from the render subnet + worker token | E-SEC-02 |
| T09 | Repudiation | Access logs leak tokens | log filter deletes Cookie, Authorization, X-Worker-Token, share/invite/reset tokens and query codes | 05 §4 |

## API (fsweb)

| ID | STRIDE | Threat | Mitigation | Requirement / control |
|---|---|---|---|---|
| T10 | Spoofing | Request on behalf of another tenant or client | tenant and client scoping in every query + PostgreSQL RLS for the runtime role | 05 §3, 04 §6 |
| T11 | Tampering | Mass assignment / tampering with immutable records | explicit pydantic request models, versions immutable, ETag/If-Match optimistic locking | C1, E-DATA-03 |
| T12 | Repudiation | User denies approving a plan or AI call | hash-chained audit_event for every state change with user, role-at-time, IP, user agent | 06 §4 |
| T13 | Information disclosure | Stack traces or client figures in errors / logs | error codes + correlation IDs only; structured logs with IDs and hashes | 05 §4, E-OPS-02 |
| T14 | Denial of service | Huge uploads or zip bombs exhaust workers | size limits at edge and API, extension + magic-byte checks, decompression limits in the reader, per-job sandboxes and timeouts | 05 §5 |
| T15 | Elevation of privilege | Self-approval (maker = checker) | approver ≠ proposer enforced server-side; plan hash bound to the approval | C2, C10 |

## Authentication

| ID | STRIDE | Threat | Mitigation | Requirement / control |
|---|---|---|---|---|
| T16 | Spoofing | Password guessing / reuse | argon2id, 12+ characters, breached-password list, lockout, mandatory TOTP, SSO for firm users | 05 §1, E-SEC-01 |
| T17 | Spoofing | Stolen session cookie used later | idle 30/60 min, absolute 12 h, sign-out-all, sessions revoked on disable | 05 §1 |
| T18 | Elevation of privilege | Privileged account misuse | just-in-time elevation approved by another admin, sealed break-glass with alerting, System Admin has no client-data access | E-SEC-03 |
| T19 | Repudiation | Disputed login | login, MFA and failure events in the audit trail; NTP-synchronised timestamps | 06 §4, E-SEC-05 |

## PostgreSQL

| ID | STRIDE | Threat | Mitigation | Requirement / control |
|---|---|---|---|---|
| T20 | Information disclosure | Bug or SQL injection returns another client's rows | parameterised ORM queries + FORCE ROW LEVEL SECURITY on all client data for fsweb_app (not the table owner) | 05 §3 |
| T21 | Tampering | Administrator edits audit rows | UPDATE/DELETE trigger on audit_event, hash chain verified nightly, head hash written to a write-once file and e-mailed | 06 §4 |
| T22 | Information disclosure | Identifiers (PAN, DIN, CIN) read from a dump | field-level envelope encryption with KMS-held KEK; encrypted backups | E-SEC-07 |
| T23 | Denial of service | Disk fills (WAL) and the database stops | WAL archiving alerts, disk alerts at 80 %, archive_timeout 5 min | 07 §6 |

## Object store (MinIO / S3)

| ID | STRIDE | Threat | Mitigation | Requirement / control |
|---|---|---|---|---|
| T24 | Tampering | A signed final is replaced or deleted | versioning on all buckets; finals in an object-locked bucket in compliance mode | 06 §1, C1 |
| T25 | Information disclosure | Bucket listing by a leaked key | least-privilege application key (object read/write only), SSE at rest, internal network only | 05 §4 |
| T26 | Elevation of privilege | Admin bypasses retention | compliance-mode retention cannot be shortened, even by root | ADR-0007 |

## Job workers (fscore)

| ID | STRIDE | Threat | Mitigation | Requirement / control |
|---|---|---|---|---|
| T27 | Tampering | Malicious workbook (macros, external links, DDE) | macros stored but never run; external links / DDE rejected; workbooks parsed, never opened in an Office app on Linux | 05 §5 |
| T28 | Information disclosure | Compromised worker exfiltrates client files | workers have no internet route; per-job sandbox wiped; read-only root filesystem | 04 §6, ADR-0020 |
| T29 | Tampering | Fix writes a corrupt or unintended change | surgical OOXML patching, strict XML validation, full cell diff, re-check, delete output on failure | C4, C5 |
| T30 | Denial of service | Pathological workbook hangs a worker | job timeouts, retries with backoff, evaluation limited to the print-sheet dependency closure | E-ARCH-05 |

## AI gateway

| ID | STRIDE | Threat | Mitigation | Requirement / control |
|---|---|---|---|---|
| T31 | Information disclosure | Client data sent to an AI provider without approval | only component with egress; payload hash must match an approval by a different user; per-engagement kill switch | C9, E-AI-05 |
| T32 | Information disclosure | Identity leakage in payloads | pseudonymisation of names, CIN, PAN, DIN, membership numbers; optional amount scaling | 02 §9.1 |
| T33 | Tampering | Prompt injection in workbook text changes outcomes | AI output is advisory, schema-validated, and every cited number is re-verified by the engine | E-AI-05 |
| T34 | Repudiation | Untraceable AI advice | prompt version, model, platform, region and payload hash recorded per call | E-AI-03 |
| T35 | Denial of service | Runaway cost | monthly budgets per firm and engagement, alert at 80 % | E-AI-04 |

## Windows render worker

| ID | STRIDE | Threat | Mitigation | Requirement / control |
|---|---|---|---|---|
| T36 | Spoofing | Rogue machine claims render jobs | shared worker token over TLS, render-subnet allow-list at the edge, token rotation | ADR-0004 |
| T37 | Denial of service | Excel hangs on a dialog | watchdog kills only the job's Excel processes; job retried on another worker; LibreOffice fallback | 11 §3, E-ARCH-03 |
| T38 | Tampering | Stale or wrong PDF produced | PDF-01 verification on every render; finals blocked on any miss | C7 |
| T39 | Information disclosure | Files left on the render host | per-job temp directory always deleted; no client data in logs | 04 §6 |

## Backups

| ID | STRIDE | Threat | Mitigation | Requirement / control |
|---|---|---|---|---|
| T40 | Tampering | Ransomware encrypts backups | off-site copy in an object-locked bucket (compliance mode); NAS snapshots; restore drills | 07 §3, C13 |
| T41 | Information disclosure | Backup media stolen | restic and pgBackRest encryption; keys split 2-of-3 among partners | E-BCP-02 |
| T42 | Denial of service | Backups silently failing | metrics + alerts for job failures, WAL lag, off-site age, missed drills | 07 §6 |

## Mode B (RDS RemoteApp, T2)

| ID | STRIDE | Threat | Mitigation | Requirement / control |
|---|---|---|---|---|
| T43 | Information disclosure | Data copied out through clipboard / drive / printer redirection | redirection disabled by policy; session time-outs; optional session recording | E-SEC-09 |
| T44 | Elevation of privilege | Excel macros or add-ins on the server | macros disabled by policy; read-only unless edit rights | E-SEC-09 |

## Mode C (offline laptop)

| ID | STRIDE | Threat | Mitigation | Requirement / control |
|---|---|---|---|---|
| T45 | Information disclosure | Laptop theft | full-disk encryption required; data directory owner-only; secrets generated per install | 07 §4 |
| T46 | Information disclosure | Network exposure of the local server | launcher binds 127.0.0.1 / ::1 only and refuses any other address | 04 §2 |
| T47 | Tampering | Tampered package imported to the server | Ed25519-signed, AES-GCM-encrypted .fspkg; signature, hash chain and base version verified; conflicts branch for Manager merge | E-DATA-05 |

## Residual risks accepted for go-live

| Risk | Why accepted | Revisit |
|---|---|---|
| Server-side Excel automation is unsupported by Microsoft | contained by watchdog, retries, fallback and PDF verification | when Microsoft offers a supported server renderer |
| Single site until HA / DR site | off-site object-locked backups, tested rebuild (RTO 4 h) | T2 warm DR site |
| Insider with legitimate access copies data | watermarks, download rights, audit trail, access reviews | DLP rules and SIEM alerts (T2) |
| Supply-chain compromise of a dependency | pinned hashes, SBOM, pip-audit / npm audit, Trivy, signed images | continuous |

_47 threats catalogued._
