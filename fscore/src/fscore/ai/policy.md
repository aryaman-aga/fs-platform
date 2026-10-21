# FS-Platform AI policy

Version: 1.0
Owner: Technical desk (approved by the partners)
Applies to: every user of FS-Platform, every engagement
Requirement: E-AI-01 (with E-AI-02…05, control C9)

## 1. Why the firm uses AI at all

FS-Platform is complete without AI. Every number, casting check, cross-reference, fix and PDF is produced by the deterministic engine. The optional AI reviewer is a second-level reader that helps a reviewer decide faster; it never decides.

## 2. Allowed uses

1. **Triage** of engine findings: genuine / presentation-only / false positive, with a one-line reason and suggested wording for a review note.
2. **Narrative review**: checking Going Concern and other narrative notes against the figures the engine computed, and suggesting corrected wording.
3. **Cross-reference suggestions**: pairs of figures that should agree but are not yet in the engagement's cross-reference map.
4. **Plain-language explanations** of complex findings for the reviewer.

## 3. Prohibited uses

- Deciding, computing or changing any figure in the financial statements.
- Editing a workbook, a PDF or a signed document, or signing anything.
- Sending a whole workbook, trial balance or PDF. Full statements may be sent only for a narrative review that the user explicitly selects, and only after approval.
- Using any channel other than FS-Platform's AI gateway (no consumer chat tools, personal accounts or browser extensions) for client data.
- Sending real client fixtures or the private fixture map to any AI service.
- Using a provider, model or region that is not on the approved list.

## 4. Data rules

- **Minimisation:** only the findings and a few neighbouring rows, or the selected narrative sentences and a table of computed figures, are sent.
- **Pseudonymisation:** client and entity names, people, related parties, CIN, PAN, GSTIN, DIN, membership numbers, FRNs, Aadhaar-like numbers and e-mail addresses are replaced with tokens before anything leaves the firm. The token map stays inside the firm.
- **Amount scaling** (optional, for the strictest engagements): all amounts are multiplied by one undisclosed factor per request.
- **Approval:** the requester sees the exact payload, its size, the estimated tokens and cost, and the destination (provider, model, region, refusal-fallback setting). A *different* authorised user approves that exact payload. A changed payload needs a new approval. Approvals expire.
- **Region:** each provider has an approved region configured explicitly; there is no default.
- **Retention:** by default only hashes of payloads and responses are kept in the audit trail. Full retention is an engagement-level choice.
- **Kill switch:** AI can be switched off per engagement and firm-wide.

## 5. Human in the loop

AI output is advisory. Every figure the AI cites is re-checked by the engine against the workbook; unverified citations are flagged. A reviewer accepts or rejects each proposal, and that decision is logged with the user and time. No AI suggestion changes a file without a user action.

## 6. Governance

- Prompts and models are versioned in the prompt and model registry; every call records the prompt version, model, platform and region.
- Any prompt or model change is a change under the firm's change-management procedure and must pass the AI evaluation set without regressing precision or recall beyond the agreed threshold.
- Monthly budgets apply per firm and per engagement; an alert is raised at 80 %, and calls that would exceed a budget are refused.
- Refusals by the model are logged; on platforms that support it a refusal is retried on Anthropic's recommended fallback model, and the approval dialog says so.
- Suspected leakage through the AI channel is a security incident handled under the incident-response plan (including CERT-In reporting where applicable).

## 7. Acceptance

Each user accepts this policy once in FS-Platform before their first AI request; re-acceptance is required when the version changes.
