#!/usr/bin/env bash
# Monthly restore drill (07 §5, control C13, E-ITGC-04). Evidence is written to
# ${FS_EVIDENCE_DIR}/restore-drill-<UTC timestamp>/ with a SHA-256 manifest, recorded as an
# audit event by fsweb, and exposed to Prometheus (RestoreDrillMissed alert).
#
# Steps
#   1. Application drill: `fsweb.manage restore-drill` exports a random engagement as .fspkg,
#      imports it into a QUARANTINE engagement, compares every file hash, re-runs the casting
#      checks and records an audit event + report (exit non-zero on any difference).
#   2. Object-backup drill: restore the latest restic snapshot of the audit-chain head files
#      from the NAS and from off-site into a scratch directory and compare with the live copies.
#   3. Database drill (--with-db): restore the latest pgBackRest backup into a throw-away
#      container, start it, and run sanity queries (row counts; audit chain verified by step 1).
#
#   ops/backup/restore_drill.sh [--with-db]
set -euo pipefail
# shellcheck source=backup-lib.sh
. "$(dirname "$(readlink -f "$0")")/backup-lib.sh"

FS_HOME="${FS_HOME:-/opt/fs-platform}"
FS_EVIDENCE_DIR="${FS_EVIDENCE_DIR:-/var/lib/fs-platform/evidence}"
METRICS_DIR="${METRICS_DIR:-/var/lib/node_exporter/textfile}"
export METRICS_DIR
COMPOSE=(docker compose --env-file "${FS_HOME}/ops/.env" -f "${FS_HOME}/ops/docker-compose.yml")
WITH_DB=0
[[ "${1:-}" == "--with-db" ]] && WITH_DB=1

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
out="${FS_EVIDENCE_DIR}/restore-drill-${stamp}"
mkdir -p "$out"
chmod 0750 "$out"
status=0

log "restore drill ${stamp}: step 1 — application package round trip"
if "${COMPOSE[@]}" exec -T api python -m fsweb.manage restore-drill >"${out}/01-app-drill.txt" 2>&1; then
  echo "PASS" >"${out}/01-app-drill.status"
else
  echo "FAIL" >"${out}/01-app-drill.status"
  status=1
fi

log "restore drill ${stamp}: step 2 — restic restore of audit-chain heads (NAS + off-site)"
for target in nas offsite; do
  scratch="/tmp/fs-restore-${target}-${stamp}"
  if "${COMPOSE[@]}" --profile backup run --rm -T \
      -v "${scratch}:/restore" --entrypoint /bin/bash backup -c "
        set -euo pipefail
        if [[ ${target} == nas ]]; then export RESTIC_REPOSITORY=\"\$RESTIC_REPOSITORY_NAS\" RESTIC_PASSWORD_FILE=\"\$RESTIC_PASSWORD_FILE_NAS\";
        else export RESTIC_REPOSITORY=\"\$RESTIC_REPOSITORY_OFFSITE\" RESTIC_PASSWORD_FILE=\"\$RESTIC_PASSWORD_FILE_OFFSITE\"; fi
        restic restore latest --host fs-platform --target /restore --include /sources/audit-heads
        cd /restore/sources/audit-heads && find . -type f -print0 | sort -z | xargs -0 -r sha256sum > /restore/restored.sha256
        cd /sources/audit-heads && find . -type f -print0 | sort -z | xargs -0 -r sha256sum > /restore/live.sha256
        # every restored head file must equal the live file (newer live files are expected)
        join -j 2 <(sort -k2 /restore/restored.sha256) <(sort -k2 /restore/live.sha256) | awk '\$2 != \$3 {bad=1} END {exit bad}'
      " >"${out}/02-restic-${target}.txt" 2>&1; then
    echo "PASS" >"${out}/02-restic-${target}.status"
  else
    echo "FAIL" >"${out}/02-restic-${target}.status"
    status=1
  fi
  rm -rf "$scratch"
done

if [[ "$WITH_DB" == "1" ]]; then
  log "restore drill ${stamp}: step 3 — pgBackRest restore into a scratch container"
  vol="fs-drill-pg-${stamp,,}"
  image="$("${COMPOSE[@]}" images -q postgres | head -n1)"
  if docker volume create "$vol" >/dev/null \
     && docker run --rm --network none -v "${vol}:/var/lib/postgresql/data" \
          -v "$("${COMPOSE[@]}" ps -q postgres | xargs -r docker inspect -f '{{ range .Mounts }}{{ if eq .Destination "/var/lib/pgbackrest" }}{{ .Name }}{{ end }}{{ end }}'):/var/lib/pgbackrest:ro" \
          -e PGBACKREST_REPO1_CIPHER_PASS="$(grep '^PGBACKREST_CIPHER_PASS=' "${FS_HOME}/ops/.env" | cut -d= -f2-)" \
          -u postgres "$image" pgbackrest --stanza=fs --type=immediate --target-action=promote --archive-mode=off restore \
          >"${out}/03-db-restore.txt" 2>&1 \
     && docker run -d --rm --name "${vol}" --network none -v "${vol}:/var/lib/postgresql/data" -u postgres "$image" \
          postgres -c archive_mode=off >/dev/null \
     && sleep 15 \
     && docker exec "${vol}" psql -U postgres -d fsweb -Atc \
          "select 'engagements', count(*) from engagement union all select 'file_versions', count(*) from file_version union all select 'audit_events', count(*) from audit_event" \
          >>"${out}/03-db-restore.txt" 2>&1; then
    echo "PASS" >"${out}/03-db-restore.status"
  else
    echo "FAIL" >"${out}/03-db-restore.status"
    status=1
  fi
  docker rm -f "${vol}" >/dev/null 2>&1 || true
  docker volume rm "$vol" >/dev/null 2>&1 || true
fi

(cd "$out" && find . -type f ! -name MANIFEST.sha256 -print0 | sort -z | xargs -0 sha256sum >MANIFEST.sha256)
if [[ "$status" == "0" ]]; then
  write_gauge fs_restore_drill fs_restore_drill_last_success_timestamp_seconds "$(date -u +%s)"
  log "restore drill ${stamp}: PASS — evidence in ${out}"
else
  log "restore drill ${stamp}: FAIL — see ${out} and ops/runbooks/restore-full.md"
fi
write_metric restore_drill "$([[ "$status" == "0" ]] && echo 1 || echo 0)"
exit "$status"
