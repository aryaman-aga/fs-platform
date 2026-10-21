# OWASP ASVS mapping (05 §5, E-SEC-08)

**Baseline:** ASVS Level 2 for the whole application; **Level 3 for authentication, session management and
file handling** (12 §4). Mapped at chapter level against ASVS 5.0 (chapter names below); requirement-level
verification is part of the pre-go-live penetration test and the internal checklist test.

| ASVS chapter | Level | How FS-Platform meets it | Verified by |
|---|---|---|---|
| Encoding and Sanitization | L2 | React output encoding; parameterised ORM queries; no HTML rendering of workbook text; CSV/XLSX exports neutralise formula injection in text cells | API tests, SAST (Bandit/Semgrep), pen test |
| Validation and Business Logic | L2 | pydantic request models (extra fields forbidden); engagement state machine; maker-checker; balancing limited to ≤ 0.20 (C3); typed-text narrative only (C6) | state-machine and SoD tests |
| Web Frontend Security | L2 | strict CSP, frame-ancestors none, no inline script, SameSite=Strict cookies, no tokens in local storage | Caddy config test, Playwright smoke |
| API and Web Service | L2 | versioned `/api/v1`, OpenAPI contract, consistent error envelope with correlation ID, request size limits, HMAC-signed webhooks (T2) | contract tests |
| File Handling | **L3** | allow-listed extensions + magic bytes, size limits at edge and API, decompression limits, macros never executed, external links / DDE rejected, ClamAV scanning (optional profile), files stored outside any web root with random keys, downloads with Content-Disposition and per-role rights, watermarking | upload validation tests, pen test |
| Authentication | **L3** | argon2id, 12+ chars, breached-password check, mandatory TOTP on the firm server (WebAuthn T2; the desktop edition may switch it off, threat model T52), lockout + rate limits, single-use invites (72 h) and resets, OIDC SSO, step-up MFA for admin actions, break-glass sealed | auth test suite |
| Session Management | **L3** | opaque DB-backed sessions, rotation at login and privilege change, idle 30/60 min and absolute 12 h limits, sign-out-all, immediate revocation on disable, CSRF tokens | session tests (frozen time) |
| Authorization | L2 | RBAC matrix enforced server-side on every endpoint, ABAC policies (locked engagements read-only, client scoping, office networks), PostgreSQL RLS, every denial audited | RBAC matrix test (allowed and denied per role), RLS test on PostgreSQL |
| Self-contained Tokens | L2 | approval tokens and share links are signed, bound to a payload hash or object, expiring, single-use | AI approval and share-link tests |
| OAuth and OIDC | L2 | Authorization Code + PKCE, state and nonce validated, issuer and audience checked, JWKS rotation handled | mocked-IdP tests |
| Cryptography | L2 | AES-256-GCM envelope encryption with a KMS-held KEK and key versions; Ed25519 package signatures; SHA-256 hashing; no custom crypto primitives | crypto unit tests, key-rotation test |
| Secure Communication | L2 | TLS 1.2+ only at the edge, HSTS; internal traffic on an isolated network; render workers over TLS with token | Caddy config test |
| Configuration | L2 | production settings validation fails closed (secret length, KMS key, secure cookies, PostgreSQL); images pinned by digest; read-only root filesystems; dependency scanning | settings tests, Trivy, pip-audit, npm audit |
| Data Protection | L2 | field-level encryption of identifiers; no client data in logs, metrics labels or alerts; retention and legal hold; DPDP workflows | log hygiene tests, DPDP admin tests |
| Secure Coding and Architecture | L2 | engine isolated from the web layer (D3), threat model per phase, two-person review for rules and security code, SBOM | CI evidence |
| Security Logging and Error Handling | L2 | hash-chained audit trail, security events (login, MFA, denials, elevation), 180-day log retention in India, NTP to NIC/NPL, generic error messages with correlation IDs | audit-chain tests, monitoring config tests |

WebRTC (ASVS 5.0 chapter 17) is not applicable.
