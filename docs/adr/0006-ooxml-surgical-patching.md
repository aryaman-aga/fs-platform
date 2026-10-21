# ADR-0006: Workbook writes by surgical OOXML patching with strict XML validation

- **Status:** Accepted
- **Date:** 2026-10-21
- **Deciders:** lead architect (autonomous build); to be ratified by the partners
- **Requirements:** 02 §7.4, 06 §2 C1/C4/C5, D4, B-T29 pattern

## Context

Corrected copies must open in Excel with identical styles, widths, merges, print areas, page setup, grouping, hidden states, comments, validations, conditional formats, names, charts and images (02 §3.3). Round-tripping through openpyxl drops or alters some of these. A real engagement produced a malformed `</f/>` that made Excel report the file as corrupt (B-T29 pattern).

## Decision

Never round-trip workbooks through openpyxl for writing. Patch the package with `zipfile` + `lxml`, touching only the target `<c>` elements (expanding shared-formula children first), refresh cached values, set `fullCalcOnLoad`, drop `calcChain.xml`, write to a temp file and rename atomically, **never** into the source path. After every write: strictly re-parse every XML part, re-open with an independent reader, diff every cell of every sheet (only planned cells may differ), self-test the evaluator, and re-run the casting checks. Any failure deletes the output (control C5).

## Consequences

- Good: Format fidelity and an evidence trail for every changed cell.
- Good: The known corruption bug class is caught by validation.
- Bad / cost: More engine code than a library round-trip; covered by fidelity and property tests.
