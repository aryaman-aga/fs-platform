#!/usr/bin/env bash
# Nightly audit-chain control (06 §4): verify the hash chain, then write the day's head hash to a
# write-once file (fsweb.manage audit-head) and mail it to the Firm Admin. Emits the
# fs_audit_chain_* metrics used by the AuditChainVerificationFailed alert.
#   ops/backup/audit-chain.sh
set -euo pipefail
# shellcheck source=backup-lib.sh
. "$(dirname "$(readlink -f "$0")")/backup-lib.sh"

FS_HOME="${FS_HOME:-/opt/fs-platform}"
METRICS_DIR="${METRICS_DIR:-/var/lib/node_exporter/textfile}"
FS_AUDIT_MAIL_TO="${FS_AUDIT_MAIL_TO:-}"
export METRICS_DIR
COMPOSE=(docker compose --env-file "${FS_HOME}/ops/.env" -f "${FS_HOME}/ops/docker-compose.yml")

now="$(date -u +%s)"
if "${COMPOSE[@]}" exec -T api python -m fsweb.manage verify-audit-chain >/tmp/fs-audit-verify.txt 2>&1; then
  verify=1
else
  verify=0
fi
tmp="$(mktemp "${METRICS_DIR}/.fs_audit_chain.XXXXXX")"
{
  echo "# HELP fs_audit_chain_verify_status 1 if the last audit hash-chain verification passed."
  echo "# TYPE fs_audit_chain_verify_status gauge"
  echo "fs_audit_chain_verify_status ${verify}"
  echo "# TYPE fs_audit_chain_last_verify_timestamp_seconds gauge"
  echo "fs_audit_chain_last_verify_timestamp_seconds ${now}"
} >"$tmp"
mv -f "$tmp" "${METRICS_DIR}/fs_audit_chain.prom"

if [[ "$verify" != "1" ]]; then
  log "audit chain verification FAILED — follow ops/runbooks/alerts/AuditChainVerificationFailed.md"
  exit 1
fi

head_out="$("${COMPOSE[@]}" exec -T api python -m fsweb.manage audit-head 2>&1)"
log "audit head recorded"
if [[ -n "$FS_AUDIT_MAIL_TO" ]] && command -v mail >/dev/null 2>&1; then
  printf 'FS-Platform audit-chain head for %s (UTC)\n\n%s\n' "$(date -u +%F)" "$head_out" \
    | mail -s "FS-Platform audit head $(date -u +%F)" "$FS_AUDIT_MAIL_TO"
fi
