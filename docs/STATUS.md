# Status

What is built and tested, what is only designed or documented, and what is deliberately deferred.
Requirement → code → test links are generated in [TRACEABILITY.md](TRACEABILITY.md)
(`python tools/traceability.py`). Tiers per the enterprise requirements: T1 before go-live, T2
within six months, T3 when scaling.

## Built and tested

### Engine (`fscore`)
- **Workbook reader** (direct OOXML, lazy backup sheets, shared-formula translation), **Excel
  number-format renderer** (displayed values, accounting formats, Indian grouping, half-away-from-zero
  rounding on 15 significant digits), **formula parser / evaluator / incremental recalculation**,
  linear-form extraction, period parsing, statement / table / key-figure detection.
- **Module R**: the full rule catalogue (`fscore/src/fscore/rules/rules.yaml`, versioned data with
  rule-set hash recorded per run) — casting (CAST, PLUG, TYPED, HID, BAL), roll-forward, cross-reference
  and mandatory tie-outs, supporting schedules, share capital, ratios, signatories / DIN / CIN / FRN /
  dates, period labels, note numbering, captions vs signs, units, formats, narrative figures and claims,
  Going Concern, related parties (TB-driven ledger and three-way reconciliation), multi-period /
  multi-entity, PDF reconciliation and PDF-only mode, evaluator self-test and sheet-state change.
- **Fix engine**: totals-only rewrite to a fixed point, typed-plug removal, missing total cells,
  cross-cast row totals, joint balancing of rounding gaps (≤ 0.20) in paise in the order PY BS → PY CF →
  CY BS → CY CF with quarter and year columns moved together, PY line-adjust mode, post-fix narrative
  restatement proposals, safe hiding of out-of-scope columns / rows; **surgical OOXML patch writer**
  (shared-formula expansion, new cells, cached-value refresh, full-calc-on-load, calc-chain removal, atomic
  write, lock-file refusal) and **post-write verification** (strict XML, independent re-open, cell diff —
  every sheet when the shared-strings or styles tables changed — self-test, full re-check, optional
  LibreOffice recalculation).
- **Module P**: TB loaders and validation gate, versioned mapping library, template library, statements
  and notes builder in house style (annual and quarterly SPFS layouts), disclosure checklist, rollover,
  supplementary-schedule bundle format, `fscore.api.prepare_statements`.
- **PDF**: Excel COM (Windows), LibreOffice and built-in renderers; draft watermark, bookmarks, versioned
  naming, final-mode gates; mandatory post-conversion verification (printed values, page mapping, ₹ glyphs,
  text cut off at the page edge); per-download watermark stamp. Where LibreOffice would not apply a sheet's
  "fit to page width" (a single text column wider than the paper), the print copy gets the scale Excel uses.
- **AI gate** (optional): pseudonymisation, payload preview + hash, approval bound to the hash with
  segregation of duties, Anthropic / Bedrock / Vertex / Foundry / local / gateway / mock providers,
  structured outputs with refusal and max-token handling, cited-number re-verification, prompt and model
  registry, budgets, 116-item evaluation set.
- **Outputs**: exceptions register, HTML report, SQLite audit store (hash-chained), workbook diff, `fscheck`
  CLI.
- **Evidence**: synthetic acceptance — 100 % of injected High errors detected individually across 64
  injection classes, ≈ 2 % unexplained findings, zero findings on clean annual and SPFS workbooks, and after
  `fix` (plus approved narrative restatements) zero findings of any rule on exact-amount books;
  hypothesis property tests (fix casts everything and is idempotent); LibreOffice independently
  recalculates every generated formula to the engine's values; performance on a 50-sheet,
  1.5-million-cell workbook: check ≈ 11 s (target < 60 s), fix + write + verify ≈ 39 s (target < 120 s).

- **Test totals** (last full run): engine 806 (including the performance test); API, tooling, Windows render
  service, offline edition and operations 4,150 (with PostgreSQL row-level security); UI 62 unit tests and 13
  Playwright end-to-end tests against a real API, worker and UI; ruff, mypy `--strict` (engine), ESLint, tsc,
  Bandit, pip-audit and npm audit clean.

### Platform (`fsweb`, `fsui`, `ops`)
- FastAPI platform with local accounts + mandatory TOTP, OIDC SSO (tested against a mocked IdP), session
  security, RBAC matrix + ABAC policies, PostgreSQL FORCE row-level security (tested as the runtime role),
  step-up MFA, just-in-time elevation, break-glass, envelope encryption of identifiers, engagement state
  machine and FINAL-lock gates, dual unlock, immutable versions, check-out locks, transactional-outbox job
  queue with idempotency, maker-checker plans / narrative / AI requests, review notes, hash-chained audit
  trail with database trigger protection, `.fspkg` signed and encrypted export / import, access reviews,
  DPDP requests, CERT-In incident clock, ITGC release log, evidence pack, metrics.
- React UI (see `fsui/README` for screens and the Playwright smoke suite).
- The signatory master kept on the platform feeds the engine's signatory checks (TXT-01); every check run
  registers the rule versions it used (evidence pack `rule_versions.csv`, linked to the approved change record).
- Access control: a lapsed JIT elevation or a disabled account never counts as holding a role (reviewer choice,
  notifications, the last-administrator guard in auto-revoke); a named access reviewer decides only while still
  holding a reviewing role; revoking a role follows the same rank rules as granting it. Emergency changes show
  when their 48-hour review falls due and is overdue (API, evidence pack, `EmergencyChangeReviewOverdue` alert),
  and a developer's refused self-approval is a recorded denial.
- Operations: Docker Compose, Helm chart, Caddy, backups (pgBackRest + restic) and scripted restore drill,
  Prometheus alerts each linked to a runbook, scheduled compliance jobs (CERT-In deadline reminders every 15
  minutes, quarterly access-review campaign, daily auto-revoke), Shamir 2-of-3 key escrow tool, Windows render
  service, CI workflow with SAST / SCA / secret scanning / SBOM.
- **Windows desktop edition** (`fsoffline`): one installer (`make windows-installer`, NSIS) bundling the
  embeddable CPython, hash-pinned win_amd64 wheels, the UI, the VC++ runtime and LibreOffice (administrative image);
  tray app (pywin32) serving 127.0.0.1 only, one server per PC across Windows accounts, data in
  `C:\ProgramData\FSPlatform` limited to an *FS-Platform Users* group, SQLite upgraded in place by Alembic
  (transactional, backup first, newer databases refused), first-run setup authorised by a one-time token, invite and
  password-reset links shown on screen, forced change of temporary passwords, daily backups (14 kept, files mirrored
  incrementally, optional second folder), a Recovery tool (reset administrator password / MFA, allow a Windows
  account, restore, diagnostics). Windows fixes found on the way: binary-safe `os.open`, `fsync` on Windows,
  extended-length object paths and Windows-safe keys, registry-proof MIME types, Excel-absent fallback to
  LibreOffice. PyMuPDF (AGPL) replaced by pypdfium2 + pypdf so the bundle is permissively licensed.
  CI: tests on Windows, installer built on macOS and exercised end to end on a clean Windows runner.
- Control evidence: [CONTROL_MATRIX.md](CONTROL_MATRIX.md) links every engagement control and IT general control
  to its framework mapping, the code that enforces it and the tests that prove it; every link is verified by
  `python tools/traceability.py --check` (and a test).

## Designed or documented, not exercised here
- **HA profile** (E-ARCH-01): Helm chart, Patroni / Redis Sentinel / distributed MinIO documented; no
  failover test was run (no cluster in this environment).
- **Container builds and IaC**: Dockerfiles, Compose, Helm and Terraform are validated statically only
  (no Docker, Helm or Terraform available here).
- **Redis rate limiter, ClamAV, Vault KMS**: written; their servers were not available for tests.
- **Excel COM rendering** runs only on Windows with licensed Excel; tested with a fake COM object (CI runners have
  no Office). Check one final PDF through Excel on a client-like PC before each desktop release.
- **Desktop tray icon and Windows sign-out handling** need an interactive Windows desktop; CI runs the installed app
  headless (`fsoffline serve`). The installer is not code-signed yet (SmartScreen warns once).
- **Load / soak tests** (E-OPS-05) beyond the single-workbook performance test; **penetration test**; DR,
  key-recovery and incident-response **drills** — procedures and runbooks exist, the drills themselves
  are organisational go-live activities.

## Deferred (T2 / T3, by decision)
- SCIM provisioning and five-minute IdP de-provisioning, WebAuthn, scoped integration API tokens
  (E-SEC-01 / E-INT-06); SIEM forwarding rules (E-SEC-11).
- Mode B (Excel on server through RDS RemoteApp) launcher and hardening (E-SEC-09) — ADR-0004 / 0002.
- Consolidation, other frameworks, multi-currency engine, XBRL (E-PREP-03…06); TB connectors beyond the
  generic / NetSuite-style loaders (E-INT-01); document-store, e-mail, signing integrations (E-INT-02…04).
- Lineage graph UI (E-DATA-04): lineage is recorded on generated cells (`CellSpec.lineage`) and the table
  exists; the click-through view is not built.

## Confirm with the firm before go-live
The phase-0 questions answered by default in [phase0-decisions.md](phase0-decisions.md) (hosting, identity
provider, AI platform and region, retention, key holders, CERT-In / DPDP contacts) and Excel-on-server
licensing ([ops/hosting.md](../ops/hosting.md)). Real-file acceptance runs locally with
`fixtures/private/golden.yaml` (`fscore/tests/test_golden_private.py`); it cannot run in this repository.
