# ADR-0010: House style: built-in Big Four-style template pack v1

- **Status:** Accepted
- **Date:** 2026-10-21
- **Deciders:** lead architect (autonomous build); to be ratified by the partners
- **Requirements:** E-RULE-02, E-PREP-02, Q7

## Context

Q7 asks for a sample signed set in the firm's house style. None is available to the build; Module P must still produce complete statements.

## Decision

Ship a **versioned house-style template pack v1** (fonts, layouts, note numbering, standard wording for Going Concern, MSME, CSR, etc.) as data in the template library (E-RULE-02), modelled on common Indian Big Four Schedule III presentation. When the firm supplies its signed sample, create pack v2 through the rule/template change control (E-ITGC-02) and pin it per engagement.

## Consequences

- Good: Module P is usable immediately; the firm's style is a data change, not a code change.
- Bad / cost: Pack v1 needs partner review before it is used for signed statements.
