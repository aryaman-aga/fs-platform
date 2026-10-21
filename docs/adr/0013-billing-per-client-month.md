# ADR-0013: Billing: usage per client per month; invoicing outside the app

- **Status:** Accepted
- **Date:** 2026-10-21
- **Deciders:** lead architect (autonomous build); to be ratified by the partners
- **Requirements:** D7, E-BIZ-01, E-BIZ-02, Q10

## Context

Q10 and D7 (own clients; per-client monthly fee). E-BIZ-02 (GST invoicing) is T3.

## Decision

Record **usage per client per month** (engagements, runs, renders) in `billing_usage` and export it; the firm invoices from its existing accounting system. GST-compliant invoice export is T3.

## Consequences

- Good: Billing data with no new financial system to secure.
- Bad / cost: Manual step to raise invoices.
