# ADR-0021: Repository hygiene: synthetic fixtures only; build pack and real workbooks never committed

- **Status:** Accepted
- **Date:** 2026-10-21
- **Deciders:** lead architect (autonomous build); to be ratified by the partners
- **Requirements:** 10 §4, 11 §3, E-ITGC-05, E-ITGC-02

## Context

This repository is public. The build pack is firm-internal and quotes figures from real workbooks; the private fixture map contains client paths. Real workbooks are needed for the golden regression on the firm's machine (10).

## Decision

Commit **only synthetic and anonymised fixtures** and documents that contain no client names, paths, DINs or real figures. The build pack, `PRIVATE_FIXTURE_MAP.md`, `fixtures/private/`, `.fspkg` packages, `.env` files and Excel lock files are git-ignored; Docker builds use whitelist ignore files so none of them can enter an image. Real-file golden tests are marked `private` and skip when the files are absent; they run only on the firm's own machine and are never uploaded to CI or any AI service. Test IDs (B-T01…B-T30) are referenced as *patterns* reproduced synthetically.

## Consequences

- Good: The code can be shared publicly without exposing client data.
- Bad / cost: Real-file regressions must be run and evidenced locally (E-ITGC-02).
