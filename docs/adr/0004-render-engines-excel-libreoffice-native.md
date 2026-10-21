# ADR-0004: PDF render engines: Windows Excel pool, LibreOffice fallback, native renderer for drafts

- **Status:** Accepted
- **Date:** 2026-10-21
- **Deciders:** lead architect (autonomous build); to be ratified by the partners
- **Requirements:** 02 §10A, 04 §3, 11 §3, E-ARCH-03, C7, D4, Q3

## Context

Excel → PDF must reproduce what the preparer sees (02 §10A, 04 §1.4). Microsoft does not support unattended server-side Office automation (11 §3) and Excel licensing on servers needs shared-computer activation or volume licences. Q3 asks whether Mode B (Excel on the server through RDS) is needed at go-live.

## Decision

Render with a **pool of Windows workers running licensed, pinned Excel via COM** (`fsrender-win`, pull-based, one Excel per job, watchdog that kills only its own Excel processes, build recorded per render). Fall back to **LibreOffice headless** (in the worker image, with metric-compatible fonts) when no Excel worker is healthy. A **built-in native renderer** (ReportLab, inside fscore) produces drafts where neither is available, e.g. CI and the offline edition; **final PDFs require Excel or LibreOffice** unless a policy explicitly allows otherwise. Every PDF, whatever the engine, passes the PDF-vs-workbook verification (PDF-01 zero misses) before it can be final. **Mode B (RDS RemoteApp) is deferred to T2** (E-SEC-09 hardening first).

## Consequences

- Good: Excel fidelity where it matters (finals); continuity when Excel is down.
- Good: Verification makes engine differences visible instead of silent.
- Bad / cost: Windows + Office licensing cost and the unsupported-automation risk (contained by the watchdog, retries and verification).
- Bad / cost: LibreOffice may differ in fonts/column widths; the verification and visual checks flag it.
