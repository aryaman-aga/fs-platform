# ADR-0012: Backups: pgBackRest + restic to NAS and India-region object storage with object lock; Shamir 2-of-3 key escrow

- **Status:** Accepted
- **Date:** 2026-10-21
- **Deciders:** lead architect (autonomous build); to be ratified by the partners
- **Requirements:** 07 §1–§6, E-BCP-02, C13, Q9, Q19

## Context

Q9 (NAS, off-site target, key holder) and Q19 (three key holders). 07 requires RPO 15 min / RTO 4 h and 3-2-1-1-0; G16 found a single backup-key holder.

## Decision

PostgreSQL: **pgBackRest** full weekly, differential daily, continuous WAL (`archive_timeout` 5 min), 35 days PITR. Objects, the pgBackRest repository, audit heads and configuration: **restic** hourly to an on-site **NAS** (separate VLAN, own snapshots) and to **India-region object storage with object lock (compliance mode)** as the immutable copy. Weekly `restic check`, monthly scripted restore drill with evidence (C13). Every backup secret is split **2-of-3 among three partners** with Shamir's Secret Sharing (`tools/shamir.py`); key-recovery drill twice a year.

## Consequences

- Good: No single person can decrypt or lose the backups.
- Good: Ransomware cannot alter the off-site copy within the lock period.
- Bad / cost: Restores need two partners for the keys — rehearse it (key-recovery drill).
