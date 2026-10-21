#!/usr/bin/env bash
# Local demo: SQLite + local object store, demo tenant with one user per role,
# job worker in the background and the API serving the built UI on 127.0.0.1.
#   ops/dev/demo.sh [port]        (default 8700; data in var/demo, reset with rm -rf var/demo)
set -euo pipefail
cd "$(dirname "$0")/../.."
PORT="${1:-8700}"
PY="${FSUI_PYTHON:-.venv/bin/python}"
DATA="var/demo"
mkdir -p "$DATA"
export FSWEB_ENV=dev
export FSWEB_DATABASE_URL="sqlite:///$PWD/$DATA/fsweb.db"
export FSWEB_STORAGE="local:$PWD/$DATA/objects"
export FSWEB_WORK_DIR="$PWD/$DATA/work"
export FSWEB_COOKIE_SECURE=false
export FSWEB_PUBLIC_BASE_URL="http://127.0.0.1:$PORT"
export FSWEB_SERVE_UI="$PWD/fsui/dist"
[ -f "$DATA/secret" ] || "$PY" -c "import secrets; print(secrets.token_hex(32))" > "$DATA/secret"
[ -f "$DATA/kms" ] || "$PY" -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())" > "$DATA/kms"
export FSWEB_SECRET_KEY="$(cat "$DATA/secret")"
export FSWEB_KMS_MASTER_KEY="$(cat "$DATA/kms")"
"$PY" -m fsweb.manage migrate >/dev/null  # every start: an existing demo database is upgraded in place
if [ ! -f "$DATA/seed.json" ]; then
  "$PY" -m fsweb.manage seed-demo > "$DATA/seed.json"
  echo "demo seeded: credentials and TOTP secrets in $DATA/seed.json (dev only)"
fi
"$PY" -m fsweb.worker > "$DATA/worker.log" 2>&1 &
WORKER=$!
"$PY" -m uvicorn fsweb.main:app --host 127.0.0.1 --port "$PORT" &
API=$!
trap 'kill "$API" "$WORKER" 2>/dev/null || true' EXIT INT TERM
wait "$API"
