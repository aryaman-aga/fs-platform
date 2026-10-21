# AI use policy (E-AI-01) — for partner approval

**Status:** draft · **Owner:** Firm Admin · **Approved by:** _partners (date)_ · Every user accepts this
policy once in the platform before the AI reviewer is available to them; the acceptance is recorded.

## 1. Principles

1. The deterministic engine decides every number, finding and fix. The AI is a **second-level reviewer**
   whose output is **advisory** (02 §3.4, E-AI-05).
2. **Offline by default.** AI is disabled unless the Firm Admin enables a provider, and it can be switched off
   per engagement.
3. **No data leaves without approval.** Each call shows the exact payload, its size, destination
   (provider, model, region) and purpose; a user other than the requester approves it; the approval is bound
   to the payload's hash.

## 2. Allowed uses

- Triage of findings (genuine error / presentation only / false positive) with a one-line reason.
- Narrative consistency review of note text against the note's own tables and Ind AS / Schedule III
  expectations (e.g. Going Concern against Ind AS 1 paras 25–26 and SA 570).
- Suggesting cross-reference pairs not yet in the confirmed map.
- Plain-language explanations of complex mismatches for reviewers.
- Suggested wording for corrections, which a user then edits and approves as typed text.

## 3. Prohibited uses

- Deciding, changing or "rounding" any figure; editing a workbook or PDF directly.
- Signing, approving, clearing review notes or releasing anything.
- Sending whole workbooks, trial balances or PDFs (only minimised extracts; "full statements" mode needs an
  explicit choice and approval).
- Sending identifiers in clear text (names, CIN, PAN, DIN, membership numbers are pseudonymised).
- Using personal or non-approved AI accounts or tools for client work, inside or outside the platform.

## 4. Data rules

- Providers and regions approved by the partners only (ADR-0008); region is mandatory configuration.
- Contracted retention terms (zero-retention where available) recorded in the provider register.
- Pseudonymisation map stays local; amounts may be scaled for the strictest engagements.
- Payload hashes always kept; full payloads kept only if the retention policy says "full".

## 5. Governance

- **Evaluation set** (E-AI-02): 100+ labelled findings and narrative paragraphs from synthetic and anonymised
  fixtures; any prompt or model change must not reduce precision / recall beyond agreed thresholds; the
  report is attached to the release ticket.
- **Prompt and model registry** (E-AI-03): versioned prompts; model ID, platform, region and settings recorded
  per call; model upgrades go through change control.
- **Budgets** (E-AI-04): monthly limits per firm and per engagement, alert at 80 %.
- **Monitoring:** refusals, invalid outputs (schema failures) and acceptance rates reviewed monthly.

## 6. Human in the loop

Every AI suggestion becomes a *proposed* item. Accepting it is a logged user action; numbers the AI cites
are re-checked by the engine before display. No AI output changes a file without a user action.
