# Staff / Article (preparer)

**You can:** upload workbooks, trial balances and PDFs; run checks; propose fix plans; edit narrative text
(needs Manager approval); request an AI review (needs another person's approval); render draft PDFs; respond
to review notes. **You cannot** approve your own plan, approve AI calls, release drafts or lock engagements.

## Typical day
1. **Home** shows your engagements, open High findings and review notes assigned to you.
2. **Files tab → Upload** the client's workbook (and the printed PDF if you have one). The platform stores it
   as an immutable version with its SHA-256 and validates the trial balance (unmapped accounts, TB not nil,
   wrong period labels block the run until fixed or a Manager overrides).
3. **Checks tab → Run checks.** Progress streams live. Filter findings by severity, rule or sheet; each
   finding shows the cell, caption, printed value, expected value, difference, rule ID, evidence and a
   suggested fix. Mark false positives *Not an issue* with a reason (it improves the rules).
4. **Fix plan tab → Propose.** Review the proposed edits grouped as totals rewrite, plug removal, balancing
   lines, typed text and hide rows/columns. Items needing judgement (DIN, roll-forward breaks, ageing vs note,
   duplicate related parties, ratio formulas) are listed for approval or manual work.
5. Ask your Manager to approve. After *Apply*, a new version appears with the verification report
   (only planned cells changed, XML valid, casting re-checked) and the *printed figures moved* list.
6. **Narrative tab.** Going Concern and other notes show each quoted figure in green (matches the statements)
   or red (does not). Edit the proposed wording as typed text and submit for approval.
7. **PDF tab → Render draft.** Drafts carry a DRAFT watermark and the run ID; the PDF-vs-workbook check lists
   any missing values and the pages that must be re-printed.
8. **Review notes.** Respond to notes raised on findings, cells or pages; only a Partner or Reviewer can clear them.

## Tips
- Re-uploading a workbook you edited in Excel creates a new version; checks re-run automatically and the
  engagement returns to *Checked*.
- Check out a workbook before editing it offline; others are warned while it is checked out.
