# FS-Platform

A firm-hosted platform for **preparing and reviewing Ind AS financial statements** (Schedule III, Division II):
casting on printed values, rounding plugs, cross-references, roll-forwards, related-party three-way
reconciliation, Going Concern and narrative figures, signatory / DIN checks, period scope, and
PDF-versus-workbook reconciliation — with a fix engine that corrects a *copy* of the workbook by
surgical OOXML patching, maker-checker approvals, a hash-chained audit trail, and an optional,
approval-gated AI reviewer.

> All client names, figures and identifiers in this repository are **synthetic** (the fictional
> "Corvus" group). Real engagement files, the firm's alias map and the acceptance oracle stay on the
> firm's machine in `fixtures/private/` (git-ignored); see [ADR-0021](docs/adr/0021-public-repo-hygiene.md).

## What is in the box

| Component | What it does |
|---|---|
| [`fscore/`](fscore/README.md) | Pure-Python engine, offline and deterministic. **Module R** (≈70 rules in a versioned catalogue), **fix engine** (totals-only rewrite to a fixed point, plug removal, joint balancing of rounding gaps, typed-text narrative, safe hiding), **Module P** (trial balance → full statements and notes in house style), PDF rendering + verification, exceptions register / HTML report, SQLite audit store, `fscheck` CLI, optional AI gate (`anthropic` SDK, `claude-opus-5-5`). |
| [`fsweb/`](fsweb/) | FastAPI platform (`/api/v1`): login + mandatory TOTP MFA, OIDC SSO, RBAC per the permission matrix + ABAC policies + PostgreSQL row-level security, engagement state machine with FINAL-lock gates, immutable file versions, check-out locks, transactional-outbox job queue, maker-checker fix plans, review notes, AI requests bound to payload hashes, hash-chained audit trail, `.fspkg` signed export/import, DPDP / CERT-In / access-review admin. |
| [`fsui/`](fsui/) | React + TypeScript SPA: dashboard, engagement workspace (files, checks, fix plan, narrative, related parties, PDF, review notes, audit), client portal, admin. |
| [`fsrender-win/`](fsrender-win/) | Windows render service: licensed Excel via COM, watchdog, pulls render jobs from the API. |
| [`fsoffline/`](fsoffline/) | Mode C single-PC edition: the same UI on `127.0.0.1`, SQLite, `.fspkg` sync. |
| [`ops/`](ops/) | Docker Compose (single node) + Helm (HA), Caddy, Cloudflare Tunnel, pgBackRest + restic backups, restore drills, Prometheus alerts with runbooks, Terraform / Ansible skeletons, BCP, ITGC procedures. |
| [`docs/`](docs/) | ADRs, architecture, STRIDE threat model, ASVS / DPDP / key management, AI policy, user and admin guides, go-live checklist, traceability and control matrices. |

## Quick start (engine only)

```bash
python3 -m venv .venv && .venv/bin/pip install -e "fscore[ai,dev]"
.venv/bin/fscheck synth demo.xlsx --inject plug_total_minus,din_mismatch   # a synthetic workbook with known errors
.venv/bin/fscheck check demo.xlsx --html                                   # findings → exceptions register + HTML report
.venv/bin/fscheck fix demo.xlsx                                            # corrected copy, verified; original untouched
.venv/bin/fscheck pdf-export "fscheck-out/demo - casting corrected.xlsx" --draft
```

```python
from fscore import api
from fscore.config import EngagementConfig

cfg = EngagementConfig()                     # or fscore.config.load_config("config.yaml")
result = api.check_workbook("FS FY26.xlsx", cfg)
plan = api.propose_fixes("FS FY26.xlsx", cfg, check_result=result)
fixed = api.apply_fixes("FS FY26.xlsx", plan, "FS FY26 - casting corrected.xlsx", config=cfg)
```

## Quick start (platform, development)

```bash
make setup            # venv + editable installs + npm ci
make try              # API + worker + built UI on http://127.0.0.1:8700 with SQLite and a seeded demo tenant
```

`make try` seeds one demo user per role on first start; their sign-in details and TOTP secrets are in
`var/demo/seed.json` (local development only — `rm -rf var/demo` resets it). The demo engagement holds a
synthetic workbook with typical review errors: run the checks, propose a fix plan as Staff, approve and
apply it as a Manager, then render and reconcile a draft PDF.

Against PostgreSQL, with hot reload:

```bash
make pg-start         # local PostgreSQL 16 on :54329 (or point FSWEB_DATABASE_URL at your own)
make demo             # migrate + seed a demo tenant with one user per role (credentials printed)
make run-api          # :8000
make run-worker
make run-ui           # :5601
```

Production: see [ops/hosting.md](ops/hosting.md) and the [admin guide](docs/admin-guide.md).

## Tests

```bash
make test-core        # engine: unit, property (hypothesis), synthetic acceptance, performance (-m slow)
make test-web         # API, RBAC matrix, audit chain, RLS on PostgreSQL
make test-ui          # UI unit tests; make test-e2e for the Playwright smoke suite
```

The synthetic acceptance suite (`fscore/tests/test_acceptance.py`) injects every error class from the
functional specification into generated workbooks and requires 100 % detection of injected High errors,
≤ 5 % unexplained findings, zero findings on clean workbooks, and — after `fix` — every printed total
casting, both balance sheets tallying and both cash flows tying, with zero unintended cell changes.

## Design principles

* Casting is on **displayed** values; fixes rewrite **totals only** and never introduce a typed plug.
* The original workbook is **never modified**; corrected copies are verified (strict XML, independent
  re-open, cell diff, evaluator self-test, full re-check, optional LibreOffice recalculation).
* Offline by default; AI is optional, pseudonymised, approval-gated per payload hash, and advisory only.
* Rules, templates and mappings are **versioned data**; every run records engine + rule-set versions.

See [docs/STATUS.md](docs/STATUS.md) for what is done and what remains,
[docs/TRACEABILITY.md](docs/TRACEABILITY.md) for requirement → code → test links, and
[docs/CONTROL_MATRIX.md](docs/CONTROL_MATRIX.md) for each control's framework mapping, the code that enforces it
and the tests that prove it (both generated and link-checked by `python tools/traceability.py --check`).
