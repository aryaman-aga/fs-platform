# ADR-0009: Retention: 7 years from the report date, configurable, with legal hold

- **Status:** Accepted
- **Date:** 2026-10-21
- **Deciders:** lead architect (autonomous build); to be ratified by the partners
- **Requirements:** 05 §4, 07 §2, E-DATA-06, E-SEC-06, Q6

## Context

Q6. SA 230 / SQC 1 require engagement documentation to be retained; the firm's minimum is 7 years (05 §4). DPDP asks for minimisation and erasure where no legal basis remains.

## Decision

Retain engagement files, versions, findings, review notes and the audit trail for **at least 7 years from the date of the report** (configurable per tenant, never below 7), with **legal hold** that suspends deletion. The audit chain is permanent; security logs are kept 180 days in India (CERT-In). Deletion only through the Firm Admin + Partner dual-approval workflow with an audit entry and a deletion certificate (E-DATA-06, T2). DPDP erasure requests are honoured for data outside the legal-retention scope (docs/security/dpdp.md).

## Consequences

- Good: Meets professional documentation duties and DPDP minimisation together.
- Bad / cost: Storage grows for 7+ years (≈ 200 GB/year planned).
