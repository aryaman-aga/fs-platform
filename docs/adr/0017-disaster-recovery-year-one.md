# ADR-0017: Disaster recovery: off-site backups and documented rebuild in year 1; warm DR site in T2

- **Status:** Accepted
- **Date:** 2026-10-21
- **Deciders:** lead architect (autonomous build); to be ratified by the partners
- **Requirements:** 07 §1, E-BCP-03, Q20

## Context

Q20 (budget for a second site / cloud DR).

## Decision

Year 1: **off-site encrypted, object-locked backups in an India region** plus IaC and a tested rebuild runbook — **RTO 4 h, RPO 15 min**. **Warm DR site** (second office or India-region cloud with async replication, failover twice a year) is **T2** (E-BCP-03).

## Consequences

- Good: Affordable, tested recovery from site loss.
- Bad / cost: Recovery is a rebuild, not a failover, until T2.
