# ADR-0016: Signing: partner DSC tokens outside the platform; UDIN captured

- **Status:** Accepted
- **Date:** 2026-10-21
- **Deciders:** lead architect (autonomous build); to be ratified by the partners
- **Requirements:** 02 §10A, E-INT-04, Q18

## Context

Q18 (DSC or Aadhaar eSign). The platform must never apply a digital signature itself (02 §10A).

## Decision

Final PDFs are exported for signing with the **partners' Class 3 DSC tokens** outside the platform; the signed PDF is uploaded back as a **locked version**, and the **UDIN** is captured and format-validated (E-INT-04, T2 integration). The platform's job ends at a verified, watermark-free final PDF.

## Consequences

- Good: No signing keys on the server.
- Bad / cost: Manual upload of signed PDFs until the T2 signing workflow.
