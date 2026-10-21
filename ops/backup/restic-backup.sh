#!/usr/bin/env bash
# restic snapshot of the backup sources to the NAS and/or the off-site object-locked bucket.
# Runs inside the `backup` container (ops/docker/backup.Dockerfile) from a systemd timer:
#   docker compose --profile backup run --rm backup nas|offsite|all
# Sources (read-only mounts): pgBackRest repository (encrypted DB backups + WAL), MinIO data
# (all file versions), audit-chain head hashes, ops configuration (incl. encrypted secrets).
set -euo pipefail
# shellcheck source=backup-lib.sh
. "$(dirname "$(readlink -f "$0")")/backup-lib.sh"

TARGET="${1:-all}"
SOURCES=(/sources/pgbackrest /sources/minio /sources/audit-heads /sources/ops-config)

backup_to() {
  local name="$1" repo="$2" pwfile="$3"
  if [[ -z "$repo" || ! -r "$pwfile" ]]; then
    log "restic[$name]: repository or password file not configured"
    write_metric "restic_${name}" 0
    return 1
  fi
  export RESTIC_REPOSITORY="$repo" RESTIC_PASSWORD_FILE="$pwfile"
  if ! restic cat config >/dev/null 2>&1; then
    log "restic[$name]: initialising repository"
    restic init
  fi
  log "restic[$name]: snapshot starting"
  if restic backup --host fs-platform --tag fs-platform --tag "$name" --exclude-caches \
      --exclude '/sources/minio/.minio.sys/tmp' "${SOURCES[@]}"; then
    write_metric "restic_${name}" 1
    log "restic[$name]: snapshot ok"
  else
    write_metric "restic_${name}" 0
    log "restic[$name]: snapshot FAILED"
    return 1
  fi
}

rc=0
case "$TARGET" in
  nas) backup_to nas "${RESTIC_REPOSITORY_NAS:-}" "${RESTIC_PASSWORD_FILE_NAS:-/nonexistent}" || rc=1 ;;
  offsite) backup_to offsite "${RESTIC_REPOSITORY_OFFSITE:-}" "${RESTIC_PASSWORD_FILE_OFFSITE:-/nonexistent}" || rc=1 ;;
  all)
    backup_to nas "${RESTIC_REPOSITORY_NAS:-}" "${RESTIC_PASSWORD_FILE_NAS:-/nonexistent}" || rc=1
    backup_to offsite "${RESTIC_REPOSITORY_OFFSITE:-}" "${RESTIC_PASSWORD_FILE_OFFSITE:-/nonexistent}" || rc=1
    ;;
  *) echo "usage: restic-backup.sh nas|offsite|all" >&2; exit 2 ;;
esac
exit "$rc"
