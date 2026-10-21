# Restore one engagement (07 §5 `restore-engagement`)

**Target:** 1 hour. **Approvals:** Firm Admin (restore of client data) before step 3; Partner before
promotion (step 6). **Controls:** C1 (source versions immutable), C13, audit trail for every step.

Nothing is ever overwritten: a restore creates a *quarantine* engagement (or new file versions) that is
compared, then promoted. The original rows and objects stay untouched.

## 1. Decide the source

| Situation | Source |
|---|---|
| A wrong file version was used / promoted | **No restore needed**: *Files → Versions → Restore as new version* (06 §5) |
| Engagement exported earlier (archive, offline hand-over) | the `.fspkg` package |
| Data deleted or corrupted in the database | point-in-time restore of PostgreSQL into a scratch instance + object versions from MinIO / restic |

## 2. Open the ticket

Record engagement ID, reason, requested point in time (IST and UTC), requester, approver.

## 3. Restore from a package (preferred)

```
$C exec api python -m fsweb.manage import-engagement --file /work/E-123.fspkg --quarantine
```

The import verifies the Ed25519 signature, the package hash chain and the base version; a tampered
package is rejected (E-DATA-05). The result is a new engagement flagged *quarantine*.

## 4. Restore from a point in time (no package)

1. Restore PostgreSQL into a scratch container (never into production):
   ```
   docker volume create fs-pitr
   docker run --rm --network none -u postgres \
     -v fs-pitr:/var/lib/postgresql/data \
     -v fs-platform_pgbackrest-repo:/var/lib/pgbackrest:ro \
     -e PGBACKREST_REPO1_CIPHER_PASS=... fs-platform-postgres:<version> \
     pgbackrest --stanza=fs --type=time "--target=2026-10-20 18:30:00+05:30" --target-action=promote restore
   docker run -d --name fs-pitr --network fs-platform_internal -v fs-pitr:/var/lib/postgresql/data \
     -u postgres fs-platform-postgres:<version> postgres -c archive_mode=off
   ```
2. Export the engagement from the scratch database with the application's exporter, pointing it at
   the scratch DB (read-only use):
   ```
   $C run --rm -e FSWEB_DATABASE_URL=postgresql+psycopg://fsweb_owner:...@fs-pitr:5432/fsweb \
     api python -m fsweb.manage export-engagement --id E-123 --out /work/E-123-pitr.fspkg
   ```
   Object versions referenced by the old rows still exist in MinIO (versioned bucket). If MinIO itself
   lost data, first restore `/sources/minio` from restic (`restic restore <snapshot> --include /sources/minio/...`)
   into a scratch MinIO.
3. Import the package into quarantine as in step 3. Remove the scratch container and volume.

## 5. Verify

- `$C exec api python -m fsweb.manage verify-audit-chain` — chain intact.
- Compare file hashes of the quarantine engagement against the expected versions (UI: *Files → Versions*,
  SHA-256 column) and re-run checks on the latest workbook version: the findings must match the last
  recorded run of that version (E-RULE-03 reproducibility).

## 6. Promote

Partner reviews the comparison and approves promotion in the UI (*Engagement → Quarantine → Promote*);
the platform records the approval and links the restored engagement to its source. Close the ticket
with the evidence (package hash, verification output, approvals).
