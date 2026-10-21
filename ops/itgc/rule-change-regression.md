# Rule-change regression control (E-ITGC-02, E-RULE-01…04)

**Applies to:** any change to a check rule (CAST-01…MULTI-05), fix policy, note template, standard
wording, mapping library default, or AI prompt/model (with E-AI-02).

## Procedure

1. **Record the regulatory driver** (if any) in the technical desk's change log: Schedule III / Ind AS / ICAI
   change → affected rules and templates (E-RULE-04).
2. **Version, never edit in place:** a new `rule_version` / `template_version` with effective-from / to dates;
   old engagements keep their pinned versions and can be re-run bit-for-bit (E-RULE-03).
3. **Golden regression run** on:
   - the synthetic fixtures (every error class injected individually and in combination) — in CI;
   - the anonymised fixtures — in CI;
   - the **private real-file fixtures** — on the firm's own machine only (`pytest -m private`), never in CI
     or any cloud service.
   Acceptance (10 §4): 100 % of injected High errors detected, ≤ 5 % false positives, zero unintended cell
   changes after fix, expected figures reproduced, re-running fix is a no-op.
4. **Compare** with the previous rule-set version: new / lost findings per fixture, explained one by one.
5. **Approve:** Partner (technical) and the developer's reviewer sign the regression report. No rule
   deploy without regression evidence attached to the release ticket.
6. **Release** the frozen rule set (`rule_set` with `frozen_at`); engagements pick it up only from its
   effective date or when a manager opts in.

## Evidence kept

Regression report (counts, differences, approvals), CI run ID, rule-set version hash, and for real-file runs
a statement signed by the person who ran them on the firm's machine (results only, no client data).
