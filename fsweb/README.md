# fsweb — FS-Platform web platform (Mode A)

FastAPI service, job worker and admin CLI around the engine (`fscore`). The web
layer never re-implements engine logic (D3): it adds identity, authorisation,
workflow, storage, jobs and the audit trail, and calls `fscore.api` from workers.

## Run it

```bash
pip install -e fscore -e fsweb                      # from the repository root
cp fsweb/.env.example .env                           # then set real values
python -m fsweb.manage migrate                       # Alembic on PostgreSQL, create_all on SQLite
python -m fsweb.manage bootstrap --tenant "Firm" --admin-email admin@firm.in
uvicorn fsweb.main:app --host 0.0.0.0 --port 8000   # API (stateless; run 2+ behind the LB in HA)
python -m fsweb.worker                               # default queue (checks, plans, fixes, renders, diffs)
python -m fsweb.worker --queues ai-gateway           # the only process allowed internet egress
```

Development with demo data (synthetic only; refused in `prod` / `offline`):

```bash
export FSWEB_ENV=dev FSWEB_DATABASE_URL=sqlite:///./var/dev.db FSWEB_STORAGE=local:./var/objects
python -m fsweb.manage migrate && python -m fsweb.manage seed-demo   # prints users, password, TOTP secrets
```

Tests: `pytest fsweb/tests` (SQLite + fake engine), `-m postgres` against
`FSWEB_TEST_PG_URL` (owner role with CREATEDB; runtime role `fsweb_app`), `-m engine`
drives the real engine end to end.

## Layout

| Module | Purpose |
|---|---|
| `settings.py` | `FSWEB_*` environment; `prod` / `offline` fail closed on insecure values |
| `db.py`, `models.py` | SQLAlchemy models for the full data model of 08 §1; RLS context per transaction |
| `migrations/` | Alembic (packaged): schema, PostgreSQL RLS policies, append-only audit trigger, grants |
| `security/` | argon2id + breached-password list, TOTP + recovery codes, sessions/CSRF, rate limits, KMS envelope encryption, OIDC |
| `authz/` | 05 §3 permission matrix as data, ABAC policy engine (`policies/abac.yaml`), request dependencies |
| `audit.py` | SHA-256 hash chain, verification, write-once head files |
| `storage.py` | local / S3 (MinIO) objects; immutable keys; WORM retention |
| `engine.py` | the only bridge to `fscore` (+ a deterministic fake for tests) |
| `jobs/`, `worker.py` | transactional-outbox queue, handlers per run kind, `run_forever` for the offline launcher |
| `services/` | workflow state machine, uploads, engagements, `.fspkg`, compliance, seeding |
| `routers/` | the REST API under `/api/v1` (OpenAPI in `openapi.json`) |
| `manage.py` | admin CLI: migrate, bootstrap, audit chain / head, packages, restore drill, evidence pack, KEK rotation, demo seed |

## Authentication flow (for UI clients)

1. `POST /api/v1/auth/login {email, password}` → sets `fs_session` (HttpOnly) and `fs_csrf` cookies,
   returns `{status: "mfa_required" | "mfa_enrollment_required", csrf_token}`.
2. `POST /api/v1/auth/mfa/verify {code}` (TOTP or recovery code), or `POST /auth/mfa/enroll` then
   `POST /auth/mfa/enroll/confirm {code}` → returns a new `csrf_token` (the session token rotates).
3. Every unsafe request carries `X-CSRF-Token: <csrf_token>`; `GET /api/v1/auth/me` returns the user,
   assignments and the effective permission map per client.
4. Administrative actions need a fresh step-up (`POST /auth/step-up {code}`) and an `X-Action-Reason` header.

Errors always have the shape `{"error": {"code", "message", "correlation_id", "details"?}}`.
