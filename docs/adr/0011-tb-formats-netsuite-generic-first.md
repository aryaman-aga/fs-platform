# ADR-0011: Trial-balance formats: NetSuite layout and generic CSV/XLSX first

- **Status:** Accepted
- **Date:** 2026-10-21
- **Deciders:** lead architect (autonomous build); to be ratified by the partners
- **Requirements:** E-DATA-01, E-DATA-02, E-INT-01, Q8, Q15

## Context

Q8 (client TB formats, mapping ownership) and Q15 (ERP connector priority). The reviewed engagements use a NetSuite saved-search trial balance with Level 1–5 descriptions (the `NS_TB_FT27` layout); other clients use Tally, SAP, Zoho.

## Decision

Year 1 supports the **NetSuite saved-search layout** and a **generic CSV/XLSX** adapter (account, description, grouping levels, period columns, FC/INR pairs), both behind the TB validation gate (E-DATA-01: nets to zero per entity/currency, opening = prior closing, no unmapped accounts, sign conventions, period labels, FX rates present). **Tally, SAP, Zoho and Oracle adapters are T2** (E-INT-01) through the adapter SDK. Mappings are owned by the engagement Manager and versioned per client and year (E-DATA-02).

## Consequences

- Good: Covers the observed engagements now; clear extension path.
- Bad / cost: Clients on other ERPs export to the generic layout until their adapter exists.
