# Go-live checklist (phase 12 ★ — all T1 items, 12 §8)

Tick each item with the evidence location (test run, report, ticket). Partner sign-off at the end.

## Architecture and resilience
- [ ] **E-ARCH-01** HA profile available and drilled (or single-node accepted by partners with the HA path documented): node kill during a test run, API recovery < 2 min, DB failover < 30 s
- [ ] **E-ARCH-02** stateless services; rolling restart with zero failed requests under load
- [ ] **E-ARCH-05** idempotent jobs + transactional outbox; duplicate submission creates no duplicate run/version

## Security and identity
- [ ] **E-SEC-01** SSO (OIDC) for firm users; disabled-in-IdP user loses access within 5 minutes
- [ ] **E-SEC-02** ABAC policies as versioned data; policy test suite covers role × status × action
- [ ] **E-SEC-03** step-up MFA + reason for admin actions; JIT elevation; sealed break-glass alerting
- [ ] **E-SEC-05** CERT-In: IR runbook + tabletop record; 180-day log retention in India verified; NTP to NIC/NPL; named point of contact
- [ ] **E-SEC-06** DPDP: data inventory, notice/consent, DSR workflow, breach drill record, Grievance Officer named
- [ ] **E-SEC-07** envelope encryption with KMS; field-level encryption; key rotation tested without downtime
- [ ] **E-SEC-08** threat model current; ASVS L2 (L3 auth/files) verified; third-party pen test with no open critical/high; vulnerability SLAs tracked
- [ ] **E-SEC-10** dynamic watermarks on viewed/downloaded PDFs; expiring single-use client links; download rights per role

## IT general controls
- [ ] **E-ITGC-01** change management live; release log reconciles 1:1 with deployments
- [ ] **E-ITGC-02** rule-change regression control; golden regression evidence attached to the rule-set release
- [ ] **E-ITGC-03** joiner/mover/leaver linked to the IdP; leaver sample removed within 1 business day
- [ ] **E-ITGC-04** operations: monitoring, backups, restore drills, capacity review, incident tickets; first monthly report

## Data, lineage and concurrency
- [ ] **E-DATA-01** TB validation gate blocks bad TBs; override needs Manager approval
- [ ] **E-DATA-02** versioned mapping library; year-on-year diff; unmapped = 0 to proceed
- [ ] **E-DATA-03** check-out/check-in locks and optimistic locking; two-user test shows no lost update

## Rules and templates
- [ ] **E-RULE-01** rule registry with versions and effective dates; runs record the rule-set version
- [ ] **E-RULE-02** template library versioned and pinned per engagement
- [ ] **E-RULE-03** a past run reproduced bit-for-bit

## Preparation module
- [ ] **E-PREP-01** year-end rollover from prior signed statements; comparatives tie to the signed PDF
- [ ] **E-PREP-02** disclosure checklist engine; unanswered items block final

## Operations and SRE
- [ ] **E-OPS-01** SLO dashboard (availability 99.5 %, API p95 < 300 ms, check p95 < 60 s, render p95 < 90 s); error budgets in release policy
- [ ] **E-OPS-02** OpenTelemetry tracing UI → API → worker → render; correlation ID in error messages
- [ ] **E-OPS-04** every alert links to a runbook; on-call rota; incident tickets with RCA
- [ ] **E-OPS-05** load test (50 users, 10 parallel checks, 60 MB workbooks) and 8-hour soak meet the SLOs; peak-season plan
- [ ] **E-OPS-07** user guide per role, admin guide, runbooks, in-app help, release notes; all users trained

## AI governance (if AI is enabled at go-live)
- [ ] **E-AI-01** AI policy approved; users accepted it
- [ ] **E-AI-02** evaluation set and release thresholds
- [ ] **E-AI-03** prompt and model registry; outputs traceable to prompt/model versions
- [ ] **E-AI-05** human-in-the-loop guarantee tested (no AI suggestion changes a file without a user action)

## Business continuity
- [ ] **E-BCP-01** BCP approved by partners
- [ ] **E-BCP-02** key escrow 2-of-3; key-recovery drill performed

## Engagement controls (06 §2)
- [ ] C1 source immutability · [ ] C2 maker-checker · [ ] C3 balancing ≤ 0.20 · [ ] C4 no new plugs · [ ] C5 post-write verification
- [ ] C6 narrative typed text · [ ] C7 PDF matches workbook · [ ] C8 signatory master · [ ] C9 AI egress approval · [ ] C10 SoD
- [ ] C11 period scope · [ ] C12 diff on re-release · [ ] C13 backups + restore drills · [ ] C14 access review

## Platform definition of done (09 §6)
- [ ] all fixtures pass with expected findings and fixed figures (synthetic in CI; real files on the firm's machine)
- [ ] RBAC matrix enforced server-side and RLS verified
- [ ] backups run and a restore drill is evidenced; the audit chain verifies
- [ ] users can work entirely through the browser (Mode A) with no client file stored on their PC

**Sign-off:** Partner _______________ Date ________ · Firm Admin _______________ Date ________
