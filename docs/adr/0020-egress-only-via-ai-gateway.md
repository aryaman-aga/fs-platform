# ADR-0020: Network egress only through the AI gateway

- **Status:** Accepted
- **Date:** 2026-10-21
- **Deciders:** lead architect (autonomous build); to be ratified by the partners
- **Requirements:** 04 §6, 05 §4, C9, D6

## Context

Client data must never leave the firm's environment except an approved AI payload (04 §1.3, 05 §4). Workers process client files and must not be able to exfiltrate them, even if compromised.

## Decision

API, workers, database and object store run on networks **without an internet route** (Docker `internal` network; default-deny Kubernetes NetworkPolicies). The **ai-gateway** is the only application component with egress, restricted to the configured provider endpoints over HTTPS, and it refuses any payload whose hash does not match an approval token. Narrow, purpose-specific exceptions: ClamAV signature updates, the backup container to its off-site bucket, Alertmanager notifications, the Cloudflare tunnel connector.

## Consequences

- Good: A compromised worker cannot reach the internet.
- Good: One place to monitor, rate-limit and audit outbound AI traffic.
- Bad / cost: Operational tasks needing internet (updates) go through the build pipeline or a package mirror.
