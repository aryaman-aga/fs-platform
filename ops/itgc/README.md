# IT general controls for the platform itself (E-ITGC-01…06)

An audit firm's own platform is scrutinised in peer reviews, quality inspections and client due-diligence
questionnaires. These procedures make it auditable; the platform keeps the evidence (release table,
audit chain, access reviews, drill records) and exports a quarterly evidence pack (E-ITGC-06).

| Control | Procedure | Evidence |
|---|---|---|
| E-ITGC-01 Change management | [change-management.md](change-management.md), [release-ticket-template.md](release-ticket-template.md) | `release` table reconciles 1:1 with deployments; CI run per release |
| E-ITGC-02 Rule-change regression | [rule-change-regression.md](rule-change-regression.md) | golden regression report + Partner/Technical approval per rule release |
| E-ITGC-03 Access management | [access-management.md](access-management.md) | joiner/mover/leaver tickets; leavers removed within 1 business day |
| E-ITGC-04 Operations | [operations.md](operations.md), [monthly-ops-report-template.md](monthly-ops-report-template.md) | monthly operations report |
| E-ITGC-05 Environment segregation (T2) | [operations.md §5](operations.md) | access lists per environment |
| E-ITGC-06 Evidence pack (T2) | `python -m fsweb.manage evidence-pack --quarter 2026Q4 --out DIR` | quarterly pack reviewed by a partner |

Mapping to ISO 27001:2022 Annex A, SOC 2 and SQM 1: `docs/control-matrix-draft.md`.
