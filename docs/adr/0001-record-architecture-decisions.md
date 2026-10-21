# ADR-0001: Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-10-21
- **Deciders:** lead architect (autonomous build); to be ratified by the partners
- **Requirements:** 11 §1, 12 §7, E-ITGC-01

## Context

The build pack (11) asks for every phase-0 decision to be recorded so later phases, auditors and new engineers can see why the platform looks the way it does. The firm's own platform is subject to peer review and quality inspection (12 G2).

## Decision

Keep Architecture Decision Records in `docs/adr/` using a short MADR layout (context, decision, consequences, requirement IDs). Number them sequentially; never edit an accepted ADR's decision — supersede it with a new ADR and link both ways. Accounting-presentation choices made while building rules are logged in ADR-0018.

## Consequences

- Good: Decisions and their reasons are reviewable in pull requests and travel with the code.
- Good: Superseded decisions stay visible (audit trail of design).
- Bad / cost: Writing ADRs costs a little time per decision.
