# ADR-0002: Hosting: single-node Docker Compose on an office server, Cloudflare Tunnel, HA profile ready

- **Status:** Accepted
- **Date:** 2026-10-21
- **Deciders:** lead architect (autonomous build); to be ratified by the partners
- **Requirements:** 04 §2, §8, 12 §3, E-ARCH-01, E-ARCH-02, E-ARCH-04, D1, D9, Q1, Q4, Q12

## Context

Questions Q1 (hosting), Q4 (domain and remote access) and Q12 (HA from day one?). The firm is small to medium (50 concurrent users, 300 engagements a year at go-live, 04 §9) but 12 requires an HA profile (E-ARCH-01) and both profiles must pass the same tests.

## Decision

Go live with the **single-node profile** (`ops/docker-compose.yml`) on an office Linux server, published through **Cloudflare Tunnel** (no inbound ports on the office router; Cloudflare Access as an extra gate; application MFA stays mandatory) at a firm sub-domain such as `fs.example-firm.in`. Keep an **India-region cloud** layout (`ops/iac/terraform/azure-centralindia`) as the alternative and as the DR target. Ship the **HA profile** (k3s + Helm chart `ops/helm/fs-platform`, Patroni/CloudNativePG, distributed MinIO, Redis Sentinel) ready for phase 9 / growth; the HA rehearsal overlay `ops/docker-compose.ha.yml` proves statelessness on one host.

## Consequences

- Good: Lowest cost and data stays on premises at go-live.
- Good: Same containers and tests for both profiles; scaling is a deployment change, not a rewrite (D9).
- Good: No exposed ports: the tunnel is outbound-only.
- Bad / cost: Single site until the HA profile or DR site exists: RTO 4 h depends on off-site backups and the restore runbook.
- Bad / cost: Dependence on Cloudflare for remote access (WireGuard VPN documented as fallback).
