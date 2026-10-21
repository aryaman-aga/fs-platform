#!/usr/bin/env bash
# First-start initialisation of the PostgreSQL cluster (runs once, as the postgres superuser).
#  - fsweb_owner: owns the database and runs Alembic migrations (incl. RLS policies, audit triggers)
#  - fsweb_app:   runtime role used by API and workers; NOT the table owner, so FORCE ROW LEVEL
#                 SECURITY applies to it (05 §3 "decorator plus PostgreSQL RLS policy")
set -euo pipefail

: "${FSWEB_OWNER_PASSWORD:?FSWEB_OWNER_PASSWORD is required}"
: "${FSWEB_APP_PASSWORD:?FSWEB_APP_PASSWORD is required}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v owner_pw="$FSWEB_OWNER_PASSWORD" -v app_pw="$FSWEB_APP_PASSWORD" <<'SQL'
CREATE ROLE fsweb_owner LOGIN PASSWORD :'owner_pw' NOSUPERUSER NOCREATEROLE;
CREATE ROLE fsweb_app   LOGIN PASSWORD :'app_pw'   NOSUPERUSER NOCREATEROLE NOCREATEDB NOBYPASSRLS;
CREATE DATABASE fsweb OWNER fsweb_owner ENCODING 'UTF8' TEMPLATE template0;
REVOKE ALL ON DATABASE fsweb FROM PUBLIC;
GRANT CONNECT ON DATABASE fsweb TO fsweb_app;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname fsweb <<'SQL'
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO fsweb_app;
-- Table-level grants are issued by the migrations (fsweb/alembic), which also enable
-- FORCE ROW LEVEL SECURITY and the append-only trigger on audit_event.
SQL

echo "fs-platform: roles fsweb_owner / fsweb_app and database fsweb created"
