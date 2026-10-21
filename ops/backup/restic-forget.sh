#!/usr/bin/env bash
# Apply the snapshot retention policy (07 §2) and prune unreferenced data.
#   docker compose --profile backup run --rm --entrypoint restic-forget.sh backup nas|offsite
# Off-site snapshots younger than the bucket's object-lock period are never selected for removal,
# so prune does not fight the lock (the immutable copy stays immutable).
set -euo pipefail
# shellcheck source=backup-lib.sh
. "$(dirname "$(readlink -f "$0")")/backup-lib.sh"

TARGET="${1:?usage: restic-forget.sh nas|offsite}"
case "$TARGET" in
  nas) export RESTIC_REPOSITORY="${RESTIC_REPOSITORY_NAS:?}" RESTIC_PASSWORD_FILE="${RESTIC_PASSWORD_FILE_NAS:?}"; extra=() ;;
  offsite)
    export RESTIC_REPOSITORY="${RESTIC_REPOSITORY_OFFSITE:?}" RESTIC_PASSWORD_FILE="${RESTIC_PASSWORD_FILE_OFFSITE:?}"
    extra=(--keep-within "${RESTIC_MIN_AGE_DAYS_OFFSITE:-31}d")
    ;;
  *) echo "usage: restic-forget.sh nas|offsite" >&2; exit 2 ;;
esac

if restic forget --host fs-platform --tag fs-platform \
    --keep-hourly "${RESTIC_KEEP_HOURLY:-48}" --keep-daily "${RESTIC_KEEP_DAILY:-35}" \
    --keep-monthly "${RESTIC_KEEP_MONTHLY:-12}" --keep-yearly "${RESTIC_KEEP_YEARLY:-8}" \
    "${extra[@]}" --prune; then
  write_metric "restic_forget_${TARGET}" 1
else
  write_metric "restic_forget_${TARGET}" 0
  exit 1
fi
