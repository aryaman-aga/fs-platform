# Full platform restore (07 §5 `restore-full`)

**Objectives:** RTO 4 hours, RPO 15 minutes (07 §1). **Approvals:** Firm Admin (incident commander)
starts it; every step is logged in the incident ticket.

Use for: host loss, unrecoverable database corruption, ransomware (restore from the **object-locked
off-site copy**, which an attacker cannot have altered), or a DR exercise.

## 0. Before you start (≈ 15 min)

- Declare an incident (incident-response.md). If ransomware or intrusion is suspected, **do not**
  reconnect old hosts; preserve them for forensics and decide CERT-In reporting (6-hour clock).
- Gather: replacement host (Ubuntu LTS, 8 vCPU / 16 GB / 1 TB SSD minimum, 04 §8), the private
  configuration repository, and the **backup keys** — reconstruct them with 2 of the 3 partners
  (`tools/shamir.py combine`, key-recovery-drill.md).

## 1. Rebuild the host (≈ 45 min)

```
ansible-playbook -i ops/iac/ansible/inventory.ini ops/iac/ansible/site.yml --limit fs-app   # or the manual steps in docs/admin-guide.md
git clone <private config repo> /opt/fs-platform && cd /opt/fs-platform
cp /secure/restore/ops.env ops/.env && chmod 600 ops/.env      # from the restic config snapshot or the escrowed copy
docker compose --env-file ops/.env -f ops/docker-compose.yml build
```

Verify NTP (`chronyc tracking`) before restoring anything: audit timestamps depend on it.

## 2. Restore PostgreSQL to the latest point (≈ 60 min)

1. Restore the pgBackRest repository from restic (NAS if intact, otherwise off-site):
   ```
   docker compose --env-file ops/.env -f ops/docker-compose.yml --profile backup run --rm \
     -v fs-platform_pgbackrest-repo:/restore-target --entrypoint /bin/bash backup -c \
     'export RESTIC_REPOSITORY=$RESTIC_REPOSITORY_OFFSITE RESTIC_PASSWORD_FILE=$RESTIC_PASSWORD_FILE_OFFSITE;
      restic restore latest --host fs-platform --include /sources/pgbackrest --target /tmp/r &&
      cp -a /tmp/r/sources/pgbackrest/. /restore-target/'
   ```
2. Restore the cluster (empty `pgdata` volume) and start only PostgreSQL:
   ```
   docker compose ... run --rm -u postgres postgres pgbackrest --stanza=fs --delta restore      # latest consistent point
   docker compose ... up -d postgres
   ```
   For a specific point in time add `--type=time "--target=<timestamp with zone>" --target-action=promote`.
3. `docker compose ... exec -u postgres postgres psql -d fsweb -c "select max(ts) from audit_event"` — note the
   recovered point; RPO = incident time − this time.

## 3. Restore object storage (≈ 60 min, parallel with step 2)

If the MinIO volume is lost, restore `/sources/minio` from restic into the `minio-data` volume
(same method as step 2.1, include `/sources/minio`), then start `minio` and `minio-init`.
Confirm versioning and object lock: `mc version info fs/fs-objects`, `mc retention info --default fs/fs-finals`.

## 4. Start and verify (≈ 30 min)

```
docker compose ... up -d
docker compose ... exec api python -m fsweb.manage verify-audit-chain      # must pass
```

- Compare the chain head with the last write-once head file / e-mail **before** the incident: the
  recovered chain must contain that head (proves no tampering up to the backup point).
- Smoke test as a test user: log in with MFA, open an engagement, download a file version and check its
  SHA-256, run a check on a synthetic fixture, render a draft PDF.
- Re-register the render workers (they reconnect by themselves; check heartbeats).
- Take a fresh full backup: `ops/backup/pgbackrest-backup.sh full`; `pgbackrest stanza-upgrade` first if
  the system identifier changed.

## 5. Close

Announce restoration (users, partners), state the data-loss window, list engagements touched in that
window (their users must re-check work done after the recovered point), and complete the RCA. Record
RTO/RPO achieved in the operations report and, for a drill, in the DR exercise record.
