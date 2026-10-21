#!/usr/bin/env bash
# Host-side wrapper: run a pgBackRest backup inside the postgres container (07 §2).
#   ops/backup/pgbackrest-backup.sh full   # weekly (Sunday)
#   ops/backup/pgbackrest-backup.sh diff   # daily (Monday-Saturday)
# Creates the stanza on first use. Writes fs_backup_pgbackrest_<type> metrics for node_exporter.
set -euo pipefail
# shellcheck source=backup-lib.sh
. "$(dirname "$(readlink -f "$0")")/backup-lib.sh"

TYPE="${1:?usage: pgbackrest-backup.sh full|diff|incr}"
case "$TYPE" in full|diff|incr) ;; *) echo "usage: pgbackrest-backup.sh full|diff|incr" >&2; exit 2 ;; esac

FS_HOME="${FS_HOME:-/opt/fs-platform}"
METRICS_DIR="${METRICS_DIR:-/var/lib/node_exporter/textfile}"
export METRICS_DIR
COMPOSE=(docker compose --env-file "${FS_HOME}/ops/.env" -f "${FS_HOME}/ops/docker-compose.yml")

pgb() { "${COMPOSE[@]}" exec -T -u postgres postgres pgbackrest --stanza=fs "$@"; }

if ! pgb info >/dev/null 2>&1 || pgb info 2>/dev/null | grep -q "status: error (missing stanza path)"; then
  log "pgbackrest: creating stanza 'fs'"
  pgb stanza-create
fi

log "pgbackrest: ${TYPE} backup starting"
if pgb --type="$TYPE" backup; then
  write_metric "pgbackrest_${TYPE}" 1
  pgb check >/dev/null && write_metric "pgbackrest_archive_check" 1 || write_metric "pgbackrest_archive_check" 0
  log "pgbackrest: ${TYPE} backup ok"
else
  write_metric "pgbackrest_${TYPE}" 0
  log "pgbackrest: ${TYPE} backup FAILED"
  exit 1
fi
