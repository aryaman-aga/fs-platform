#!/usr/bin/env bash
# Backup freshness collector (07 §6), every 5 minutes from a systemd timer. Emits Prometheus
# textfile metrics that the alert rules use:
#   fs_backup_pgbackrest_last_backup_timestamp_seconds{type}
#   fs_backup_wal_last_archived_timestamp_seconds, fs_backup_wal_pending_segments
#   fs_backup_snapshot_last_timestamp_seconds{target="nas|offsite"}
set -euo pipefail
# shellcheck source=backup-lib.sh
. "$(dirname "$(readlink -f "$0")")/backup-lib.sh"

FS_HOME="${FS_HOME:-/opt/fs-platform}"
METRICS_DIR="${METRICS_DIR:-/var/lib/node_exporter/textfile}"
export METRICS_DIR
COMPOSE=(docker compose --env-file "${FS_HOME}/ops/.env" -f "${FS_HOME}/ops/docker-compose.yml")

to_epoch() { date -u -d "$1" +%s 2>/dev/null || echo 0; }

# --- pgBackRest: last backup per type and newest archived WAL
info="$("${COMPOSE[@]}" exec -T -u postgres postgres pgbackrest --stanza=fs info --output=json 2>/dev/null || echo '[]')"
tmp="$(mktemp "${METRICS_DIR}/.fs_backup_pgbackrest.XXXXXX")"
{
  echo "# HELP fs_backup_pgbackrest_last_backup_timestamp_seconds Stop time of the newest pgBackRest backup by type."
  echo "# TYPE fs_backup_pgbackrest_last_backup_timestamp_seconds gauge"
  for t in full diff incr; do
    ts="$(jq -r --arg t "$t" '[.[0].backup[]? | select(.type == $t) | .timestamp.stop] | max // 0' <<<"$info")"
    echo "fs_backup_pgbackrest_last_backup_timestamp_seconds{type=\"${t}\"} ${ts}"
  done
} >"$tmp"
mv -f "$tmp" "${METRICS_DIR}/fs_backup_pgbackrest.prom"

# --- WAL archiving: time of the last archived segment, and how many COMPLETED segments still wait
# (the current segment is always open; an idle database therefore shows 0 pending, not lag).
wal_sql="with s as (select pg_walfile_name(pg_current_wal_lsn()) as cur, last_archived_wal as arch,
  coalesce(extract(epoch from last_archived_time)::bigint, 0) as ts from pg_stat_archiver)
select ts, case when arch ~ '^[0-9A-F]{24}' then greatest(
  (('x' || substr(cur, 9, 8))::bit(32)::bigint * 256 + ('x' || substr(cur, 17, 8))::bit(32)::bigint)
  - (('x' || substr(arch, 9, 8))::bit(32)::bigint * 256 + ('x' || substr(arch, 17, 8))::bit(32)::bigint) - 1, 0)
  else 0 end from s"
wal_row="$("${COMPOSE[@]}" exec -T -u postgres postgres psql -At -F ' ' -c "$wal_sql" 2>/dev/null || echo '0 0')"
read -r wal_ts wal_pending <<<"${wal_row:-0 0}"
tmp="$(mktemp "${METRICS_DIR}/.fs_backup_wal.XXXXXX")"
{
  echo "# TYPE fs_backup_wal_last_archived_timestamp_seconds gauge"
  echo "fs_backup_wal_last_archived_timestamp_seconds ${wal_ts:-0}"
  echo "# TYPE fs_backup_wal_pending_segments gauge"
  echo "fs_backup_wal_pending_segments ${wal_pending:-0}"
} >"$tmp"
mv -f "$tmp" "${METRICS_DIR}/fs_backup_wal.prom"

# --- restic: newest snapshot per target (read-only listing)
for target in nas offsite; do
  snap="$("${COMPOSE[@]}" --profile backup run --rm -T --entrypoint /bin/bash backup -c "
      set -euo pipefail
      if [[ ${target} == nas ]]; then export RESTIC_REPOSITORY=\"\$RESTIC_REPOSITORY_NAS\" RESTIC_PASSWORD_FILE=\"\$RESTIC_PASSWORD_FILE_NAS\";
      else export RESTIC_REPOSITORY=\"\$RESTIC_REPOSITORY_OFFSITE\" RESTIC_PASSWORD_FILE=\"\$RESTIC_PASSWORD_FILE_OFFSITE\"; fi
      restic snapshots --host fs-platform --latest 1 --json --no-lock
    " 2>/dev/null || echo '[]')"
  ts_text="$(jq -r 'map(.time) | max // empty' <<<"$snap" 2>/dev/null || true)"
  ts=0
  [[ -n "$ts_text" ]] && ts="$(to_epoch "$ts_text")"
  write_gauge "fs_backup_snapshot_${target}" fs_backup_snapshot_last_timestamp_seconds "$ts" "target=\"${target}\""
done

log "backup metrics refreshed"
