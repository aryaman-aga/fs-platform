#!/usr/bin/env bash
# Scheduled compliance jobs on the single-node profile (timers in ops/backup/systemd; the Helm chart runs
# the same commands as CronJobs):
#   incident-check       every 15 minutes: CERT-In six-hour reporting deadline reminders (E-SEC-05)
#   auto-revoke          daily: revoke access-review items left undecided past their deadline (C14)
#   start-access-review  quarterly: open the access-review campaign for the quarter (C14)
#   ops/backup/compliance-job.sh <command>
set -euo pipefail
# shellcheck source=backup-lib.sh
. "$(dirname "$(readlink -f "$0")")/backup-lib.sh"

FS_HOME="${FS_HOME:-/opt/fs-platform}"
COMPOSE=(docker compose --env-file "${FS_HOME}/ops/.env" -f "${FS_HOME}/ops/docker-compose.yml")

case "${1:-}" in
  incident-check | auto-revoke | start-access-review) ;;
  *)
    echo "usage: $0 incident-check|auto-revoke|start-access-review" >&2
    exit 2
    ;;
esac
log "fsweb.manage $1"
"${COMPOSE[@]}" exec -T api python -m fsweb.manage "$1"
