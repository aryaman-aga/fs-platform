# Backups, restore drills and key escrow (07, E-BCP-02, control C13)

| What | Tool | When | Retention | Script |
|---|---|---|---|---|
| PostgreSQL | pgBackRest full + differential + continuous WAL (`archive_timeout=300`) | full Sun, diff Mon–Sat, WAL ≤ 5 min | 35 days PITR in the local repo; monthly 12 m, yearly 8 y via restic snapshots of the repo | `pgbackrest-backup.sh` |
| Objects (all file versions, PDFs, reports) | MinIO versioning + object lock (finals); restic snapshot | hourly | ≥ 7 years (engagement retention) | `restic-backup.sh` |
| Config + encrypted secrets, Caddy, compose | git (private) + restic | on change, hourly | 1 year+ | `restic-backup.sh` |
| Audit-chain head hashes | write-once files + e-mail | nightly | permanent | `audit-chain.sh` |
| Windows render server | image backup (Windows Server Backup / Veeam Agent) | weekly | 4 weeks | outside this repo |

**3-2-1-1-0:** live volumes (1) → NAS on a separate VLAN with its own snapshots (2, other medium) →
off-site India-region object storage with **object lock, compliance mode** (3, the immutable copy);
weekly `restic check` and the monthly restore drill give the "0 errors" evidence.

## Install (single node)

```
cp ops/backup/restic.env.example ops/backup/restic.env && chmod 600 ops/backup/restic.env
install -d -m 0750 /var/lib/fs-platform/evidence /var/lib/node_exporter/textfile
cp ops/backup/systemd/*.service ops/backup/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now fs-pgbackrest-full.timer fs-pgbackrest-diff.timer fs-restic-nas.timer \
  fs-restic-offsite.timer fs-restic-forget.timer fs-restic-check.timer fs-backup-metrics.timer \
  fs-audit-chain.timer fs-restore-drill.timer
ops/backup/pgbackrest-backup.sh full      # first full backup + stanza creation
```

(`crontab.example` is the cron equivalent.) The server clock is UTC and synchronised to the NIC /
NPL time servers (`ops/ntp/chrony.conf`, CERT-In).

## Encryption keys and escrow (E-BCP-02)

Three secrets protect the backups: the restic NAS password, the restic off-site password and the
pgBackRest cipher pass. Each is split **2-of-3** among three partners with `tools/shamir.py`:

```
openssl rand -base64 48 | tr -d '\n' > /run/secrets/restic-offsite.pass
tools/shamir.py split --k 2 --n 3 --secret-file /run/secrets/restic-offsite.pass \
    --out-dir /secure/usb --label restic-offsite-2026
```

Hand each `share-N-of-3.txt` to a different partner (sealed envelope or hardware token), store
nothing else together, and test recovery in the key-recovery drill
(`ops/runbooks/key-recovery-drill.md`) without revealing the secret:
`tools/shamir.py verify share-1-of-3.txt share-3-of-3.txt`.

## Restore

* One engagement: `ops/runbooks/restore-engagement.md` (package or point-in-time, into **quarantine**,
  promoted after Partner approval).
* Whole platform: `ops/runbooks/restore-full.md` (RTO 4 h).
* Monthly drill: `restore_drill.sh --with-db` (systemd `fs-restore-drill.timer`) writes evidence to
  `/var/lib/fs-platform/evidence/restore-drill-<UTC>/` with a SHA-256 manifest and updates the
  `fs_restore_drill_last_success_timestamp_seconds` metric (alert `RestoreDrillMissed` after 35 days).

## Monitoring (07 §6)

`backup_metrics.sh` (every 5 min) and every backup script write Prometheus textfile metrics that
node_exporter publishes. Alerts (`ops/monitoring/alerts.yml`): `BackupJobFailed`,
`WalArchiveLagHigh` (> 10 min), `OffsiteBackupStale` (> 26 h), `RestoreDrillMissed`,
`DiskUsageHigh` (> 80 %), `AuditChainVerificationFailed`. A weekly status e-mail is produced by
Alertmanager's summary route and the monthly operations report (`ops/itgc/monthly-ops-report-template.md`).
