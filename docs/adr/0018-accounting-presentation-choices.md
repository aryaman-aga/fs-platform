# ADR-0018: Accounting-presentation choices log

- **Status:** Accepted
- **Date:** 2026-10-21
- **Deciders:** lead architect (autonomous build); to be ratified by the partners
- **Requirements:** 03 §3, 02 §2.4, 02 §7, C3, C6, MULTI-02, MULTI-04

## Context

The master prompt asks that uncertain presentation choices take the Ind AS / Schedule III-compliant option and be logged here. Firm preferences in 03 §3 are non-negotiable.

## Decision

Choices in force: (1) casting is fixed at **totals and subtotals only** — line items never change to make totals cast; (2) typed rounding plugs are removed; the **final rounding gap** goes to the *Other expenses / Miscellaneous expenses* line of the Other-expenses note (quarter and year columns of the same period end move together, PY solved before CY) and the cash-flow gap to *(Decrease)/increase in other current liabilities* — **only gaps ≤ 0.20** are balanced (C3); larger gaps are reported; (3) **cash-flow gaps caused by exchange differences on cash and translation differences are reported, never plugged** (MULTI-04); (4) narrative notes (Going Concern etc.) are corrected as **typed text with 2 decimals in the statement unit**, never formulas, never rounded to 1 decimal (C6); (5) out-of-scope columns/rows are **hidden, never deleted**, and only if hiding cannot hide anything else (MULTI-02); (6) heading and structure changes are listed and applied only after approval; (7) comparatives must equal the **signed prior-year statements**, with PY line-adjust mode available and every PY change flagged.

## Consequences

- Good: Consistent, explainable corrections that a reviewer can trace.
- Bad / cost: Some files need manual work where the policy forbids an automatic fix.
