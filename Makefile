# FS-Platform developer tasks. Python venv in .venv, Node in fsui/.
PY      ?= .venv/bin/python
PIP     ?= .venv/bin/pip
PGBIN   ?= /opt/homebrew/opt/postgresql@16/bin
PGPORT  ?= 54329
export FSWEB_TEST_PG_URL ?= postgresql+psycopg://fsweb_owner:fsweb_owner@127.0.0.1:$(PGPORT)/fsweb_test

.PHONY: help setup lint typecheck test test-core test-web test-ui test-e2e test-all fixtures fixtures-freeze \
        run-api run-worker run-ui pg-start pg-stop sbom audit build-ui demo try windows-lock windows-installer clean

help:  ## list targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-16s %s\n", $$1, $$2}'

setup:  ## create the venv and install every package in editable mode
	python3 -m venv .venv
	$(PIP) install -U pip wheel
	$(PIP) install -e "fscore[ai,dev]" -e fsweb -e fsrender-win -e fsoffline
	cd fsui && npm ci

lint:  ## ruff + eslint
	$(PY) -m ruff check .
	cd fsui && npm run lint

typecheck:  ## mypy --strict on the engine + tsc
	$(PY) -m mypy fscore/src
	cd fsui && npm run typecheck

test-core:  ## engine unit, property and synthetic-fixture tests
	$(PY) -m pytest fscore/tests -m "not slow and not private" -n auto

test-web:  ## API, RBAC matrix, audit chain, RLS (needs PostgreSQL: make pg-start)
	$(PY) -m pytest fsweb/tests -n auto

test-ui:  ## UI unit tests
	cd fsui && npm test

test-e2e:  ## Playwright smoke against a real backend
	cd fsui && npm run e2e

test-all: test-core test-web test-ui  ## everything except e2e
	$(PY) -m pytest tools/tests fsrender-win/tests fsoffline/tests ops/tests

fixtures:  ## generate the synthetic fixture set (clean + every injected error class)
	$(PY) tools/make_synthetic_workbook.py --all fixtures/synthetic/out

fixtures-freeze:  ## fingerprint the firm's private real-file fixtures (never committed)
	$(PY) tools/fixtures_freeze.py

pg-start:  ## local PostgreSQL 16 for tests
	@test -d .pgdata || $(PGBIN)/initdb -D .pgdata -U postgres --auth=trust -E UTF8 --locale=C >/dev/null
	LC_ALL=en_US.UTF-8 $(PGBIN)/pg_ctl -D .pgdata -o "-p $(PGPORT) -k /tmp" -l .pgdata/server.log start

pg-stop:
	$(PGBIN)/pg_ctl -D .pgdata stop

run-api:  ## API on :8000 (dev settings)
	FSWEB_ENV=dev $(PY) -m uvicorn fsweb.main:app --reload --port 8000

run-worker:  ## job worker (checks, fixes, renders)
	FSWEB_ENV=dev $(PY) -m fsweb.worker

run-ui:  ## UI dev server on :5601 (proxies /api to :8000)
	cd fsui && npm run dev

build-ui:
	cd fsui && npm run build

demo:  ## seed a demo tenant with users for every role
	FSWEB_ENV=dev $(PY) -m fsweb.manage migrate && FSWEB_ENV=dev $(PY) -m fsweb.manage seed-demo

try: build-ui  ## everything on http://127.0.0.1:8700 (SQLite, demo users in var/demo/seed.json)
	ops/dev/demo.sh 8700

windows-lock:  ## resolve the Windows desktop bundle's packages (hashes; versions pinned to this dev environment)
	mkdir -p build/windows
	$(PIP) freeze --exclude-editable | grep -v -E '^(fscore|fsweb|fsoffline|fsrender)' > build/windows/dev-constraints.txt
	$(PY) -m uv pip compile fsoffline/packaging/requirements-windows.txt --python-platform x86_64-pc-windows-msvc \
	  --python-version 3.13 --generate-hashes --no-annotate --constraint build/windows/dev-constraints.txt \
	  --custom-compile-command "make windows-lock" -o fsoffline/packaging/requirements-windows.lock

windows-installer:  ## build dist/windows/FS-Platform-Setup-<version>.exe (needs makensis; runs on macOS, Linux or Windows)
	$(PY) fsoffline/packaging/build_windows.py

sbom:  ## CycloneDX SBOM for the Python packages
	$(PY) -m cyclonedx_py environment -o sbom.json

audit:  ## dependency vulnerability scans
	$(PY) -m pip_audit
	cd fsui && npm audit --audit-level=high

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache var/scratch fsui/dist
