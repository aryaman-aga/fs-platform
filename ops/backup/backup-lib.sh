#!/usr/bin/env bash
# Shared helpers for the backup scripts: Prometheus textfile metrics (node_exporter collects
# ${METRICS_DIR}/*.prom) and logging without secrets. Source it; do not execute it.
set -euo pipefail

METRICS_DIR="${METRICS_DIR:-/metrics}"

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }

# write_metric <kind> <status 1|0>  — keeps the previous last-success timestamp on failure.
write_metric() {
  local kind="$1" status="$2" now file tmp last_success
  now="$(date -u +%s)"
  mkdir -p "$METRICS_DIR"
  file="${METRICS_DIR}/fs_backup_${kind}.prom"
  last_success="0"
  if [[ -f "$file" ]]; then
    last_success="$(awk '/^fs_backup_last_success_timestamp_seconds/ {print $2}' "$file" | tail -n1)"
    last_success="${last_success:-0}"
  fi
  if [[ "$status" == "1" ]]; then
    last_success="$now"
  fi
  tmp="$(mktemp "${METRICS_DIR}/.fs_backup_${kind}.XXXXXX")"
  {
    echo "# HELP fs_backup_last_run_status 1 if the last ${kind} run succeeded, 0 if it failed."
    echo "# TYPE fs_backup_last_run_status gauge"
    echo "fs_backup_last_run_status{kind=\"${kind}\"} ${status}"
    echo "# HELP fs_backup_last_run_timestamp_seconds Unix time of the last ${kind} run."
    echo "# TYPE fs_backup_last_run_timestamp_seconds gauge"
    echo "fs_backup_last_run_timestamp_seconds{kind=\"${kind}\"} ${now}"
    echo "# HELP fs_backup_last_success_timestamp_seconds Unix time of the last successful ${kind} run."
    echo "# TYPE fs_backup_last_success_timestamp_seconds gauge"
    echo "fs_backup_last_success_timestamp_seconds{kind=\"${kind}\"} ${last_success}"
  } >"$tmp"
  chmod 0644 "$tmp"
  mv -f "$tmp" "$file"
}

# write_gauge <file-stem> <metric> <value> [labels]
write_gauge() {
  local stem="$1" metric="$2" value="$3" labels="${4:-}" tmp
  mkdir -p "$METRICS_DIR"
  tmp="$(mktemp "${METRICS_DIR}/.${stem}.XXXXXX")"
  {
    echo "# TYPE ${metric} gauge"
    if [[ -n "$labels" ]]; then echo "${metric}{${labels}} ${value}"; else echo "${metric} ${value}"; fi
  } >"$tmp"
  chmod 0644 "$tmp"
  mv -f "$tmp" "${METRICS_DIR}/${stem}.prom"
}
