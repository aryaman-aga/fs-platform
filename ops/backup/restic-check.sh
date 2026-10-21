#!/usr/bin/env bash
# Weekly repository integrity check ("0 errors in verified restores", 07 §1): structure check plus
# reading a rotating 1/52 subset of the data, so the whole repository is re-read every year.
#   docker compose --profile backup run --rm --entrypoint restic-check.sh backup nas|offsite
set -euo pipefail
# shellcheck source=backup-lib.sh
. "$(dirname "$(readlink -f "$0")")/backup-lib.sh"

TARGET="${1:?usage: restic-check.sh nas|offsite}"
case "$TARGET" in
  nas) export RESTIC_REPOSITORY="${RESTIC_REPOSITORY_NAS:?}" RESTIC_PASSWORD_FILE="${RESTIC_PASSWORD_FILE_NAS:?}" ;;
  offsite) export RESTIC_REPOSITORY="${RESTIC_REPOSITORY_OFFSITE:?}" RESTIC_PASSWORD_FILE="${RESTIC_PASSWORD_FILE_OFFSITE:?}" ;;
  *) echo "usage: restic-check.sh nas|offsite" >&2; exit 2 ;;
esac

week="$(date -u +%V)"
week=$((10#$week))
subset="$(( (week % 52) + 1 ))/52"
log "restic[$TARGET]: check with --read-data-subset=${subset}"
if restic check --read-data-subset="$subset"; then
  write_metric "restic_check_${TARGET}" 1
else
  write_metric "restic_check_${TARGET}" 0
  exit 1
fi
