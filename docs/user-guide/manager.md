# Manager

**You can:** everything Staff can, plus approve and apply fix plans (not your own), approve balancing
adjustments with a checker, approve AI calls requested by others, release drafts to the client, clear the
TB-validation override, assign the team and create clients/engagements.

## Approving a fix plan (control C2)
1. **Fix plan tab** shows the plan hash, who proposed it and each edit (old → new formula, value before →
   after, reason, finding IDs).
2. Tick the *approval* items you accept (links to sources, DIN fixes, captions, ratio formulas). *Auto* items
   follow the firm's policy. Balancing items above 0.20 are never offered — they appear as *investigate*.
3. Approve. The approval records the plan hash: if anyone changes the plan afterwards, it needs approval again.
4. Apply. The platform writes a new version, verifies it, and moves the engagement to *Fixed and verified*
   only if every verification step passed.

## Related parties
The **Related parties tab** shows the three-way reconciliation per counterparty × nature × period
(RPT note vs trial balance vs financial-statement note line) with omitted balances, wrong counterparties,
full-year columns equal to the quarter, gross vs net differences and classification switches.

## Releasing a draft to the client
**PDF tab → Release to client** — the release includes a diff against the last released draft (C12) and a
watermarked PDF; clients see only released drafts and items you share.

## AI review approvals
You see the exact pseudonymised payload, its size, provider, model and region. Approve only what is
necessary; approval is bound to the payload hash.
