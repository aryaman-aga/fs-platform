# ADR-0007: Object storage: MinIO / S3 with versioning and object lock; local adapter for Mode C

- **Status:** Accepted
- **Date:** 2026-10-21
- **Deciders:** lead architect (autonomous build); to be ratified by the partners
- **Requirements:** 04 §3, 06 §1, 07 §2, C1, D2

## Context

Uploads and produced files are immutable versions (C1); FINAL files must be WORM (06 §1). Mode C has no object store. MinIO's community distribution and licensing (AGPL-3.0) changed during 2025; any S3-compatible store with versioning and object lock satisfies the design.

## Decision

Use an **S3-compatible store** — MinIO on premises (bucket `fs-objects` with versioning; bucket `fs-finals` created **with object lock, compliance mode, 7-year default retention**) or object storage in an India region. The application talks S3 only; a **local filesystem adapter** serves Mode C and tests. Server-side encryption is on; the application uses a least-privilege access key (no bucket administration).

## Consequences

- Good: Finals cannot be deleted or overwritten, even by administrators.
- Good: Storage is swappable without code changes.
- Bad / cost: Compliance-mode retention cannot be shortened — choose the period deliberately (ADR-0009).
- Bad / cost: Pin MinIO images by digest and track the vendor's distribution policy.
