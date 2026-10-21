/**
 * In-app contextual help (E-OPS-07): one entry per screen, matched on the
 * route. Plain language for preparers, reviewers and partners.
 */

export interface HelpEntry {
  title: string;
  summary: string;
  steps?: string[];
  controls?: string[];
}

const SESSION_CONTROLS = ["Sessions end after 60 minutes idle (30 for client users) and 12 hours in total.", "Five failed attempts lock the account for 15 minutes."];

/** `withoutMfa`: the entry where the installation signs in with e-mail and password only (no authenticator app). */
const HELP: { match: RegExp; entry: HelpEntry; withoutMfa?: HelpEntry }[] = [
  {
    match: /^\/login|^\/signup|^\/mfa|^\/invite|^\/reset-password|^\/forgot-password/,
    entry: {
      title: "Signing in",
      summary: "Every user signs in with a password and a second factor (authenticator app). Recovery codes are shown once when you enrol.",
      steps: ["Enter your firm e-mail and password.", "Enter the 6-digit code from your authenticator app.", "Lost your phone? Use a recovery code, then ask the Firm Admin to reset MFA."],
      controls: SESSION_CONTROLS,
    },
    withoutMfa: {
      title: "Signing in",
      summary: "Sign in with your e-mail and password.",
      steps: ["Enter your e-mail and password.", "New here? “Create an account” on the sign-in page, when it is offered.", "Forgot your password? Use “Forgot password?” on the sign-in page."],
      controls: SESSION_CONTROLS,
    },
  },
  {
    match: /^\/engagements\/[^/]+\/checks/,
    entry: {
      title: "Checks and findings",
      summary: "Run the review engine on the latest workbook. Findings list the sheet, cells, printed and expected values, evidence and a suggested fix.",
      steps: [
        "Click Run checks; progress streams live.",
        "Filter by severity, rule or sheet; press ↑/↓ to move through findings and Enter to open one.",
        "Set a status. 'Not an issue' needs a reason — it feeds the rule-quality analytics.",
        "Raise a review note or propose a fix plan from a finding.",
      ],
      controls: ["Casting is on printed (rounded) values, not stored values.", "Client users see only findings shared with them."],
    },
  },
  {
    match: /^\/engagements\/[^/]+\/fix/,
    entry: {
      title: "Fix plan (maker-checker)",
      summary: "The fix engine proposes edits to a copy of the workbook: totals rewritten to add printed components, typed plugs removed, rounding-sized balancing lines.",
      steps: ["Propose a plan from the latest check.", "A different person approves it; the approval records the plan hash.", "Apply: a new version is written and verified (XML, cell diff, re-check)."],
      controls: ["C2: the proposer cannot approve.", "C3: balancing is limited to rounding (≤ 0.20).", "C5: the copy is verified before it is kept; the original is never modified (C1)."],
    },
  },
  {
    match: /^\/engagements\/[^/]+\/narrative/,
    entry: {
      title: "Narrative notes",
      summary: "Figures quoted in Going Concern and other notes are checked against the statements: green matches, red does not.",
      steps: ["Review the highlighted sentences.", "Edit the proposed wording — typed text in 2 decimals, never a formula.", "Submit; a Manager approves edits made by Staff; then apply approved edits as a new version."],
      controls: ["C6: formulas are rejected in narrative cells."],
    },
  },
  {
    match: /^\/engagements\/[^/]+\/pdf/,
    entry: {
      title: "PDF and release",
      summary: "Render a draft or final PDF from the latest workbook. Every render is reconciled with the workbook (PDF-01) before it can be released.",
      controls: ["C7: a PDF with missing values cannot be released.", "A FINAL PDF needs zero High findings and passing signatory checks.", "Downloads are watermarked with your name and the time."],
    },
  },
  {
    match: /^\/engagements\/[^/]+\/files/,
    entry: {
      title: "Files and versions",
      summary: "Uploads become immutable versions. Check a workbook out before editing it elsewhere so nobody else overwrites your work.",
      controls: ["C1: versions are never overwritten; restore creates a new version.", "Accepted types: .xlsx, .xlsm (macros are never run), .pdf, .csv."],
    },
  },
  {
    match: /^\/engagements\//,
    entry: {
      title: "Engagement workspace",
      summary: "Everything for one set of statements: files, checks, fixes, narrative, related parties, PDF, review notes, checklist, AI review, audit timeline and settings.",
      steps: ["The status rail shows where the engagement is in the 06 §1 workflow.", "Buttons only appear for actions your role allows; the server checks every request."],
    },
  },
  {
    match: /^\/clients/,
    entry: {
      title: "Clients and entities",
      summary: "Client master data: entities with functional and presentation currency, the group tree and the signatory master (names, roles, DIN).",
      controls: ["PAN, CIN and DIN are stored encrypted and shown masked."],
    },
  },
  {
    match: /^\/portal/,
    entry: {
      title: "Client portal",
      summary: "See drafts released to you, answer queries from the audit team, upload requested files and confirm management representations.",
    },
  },
  {
    match: /^\/admin/,
    entry: {
      title: "Administration",
      summary: "Users, roles and permissions, access reviews, AI policy, backups, incidents, data-principal requests, releases, usage and health.",
      controls: ["Administrative changes need a reason and a fresh MFA code (step-up).", "Every change is in the hash-chained audit trail."],
    },
    withoutMfa: {
      title: "Administration",
      summary: "Users, roles and permissions, access reviews, AI policy, backups, incidents, data-principal requests, releases, usage and health.",
      steps: ["Someone who created their own account sees no clients until you give them a role: Users shows them as “No role yet”, Assignments gives the role."],
      controls: ["Administrative changes need a reason, and your password again if you last confirmed it more than 5 minutes ago.", "Every change is in the hash-chained audit trail."],
    },
  },
  {
    match: /^\/audit/,
    entry: { title: "Audit trail", summary: "Append-only, hash-chained events. Verify the chain and export CSV for the engagement archive." },
  },
  {
    match: /^\/$/,
    entry: { title: "Dashboard", summary: "Your engagements with status, due dates, open High findings and review notes, plus recent activity." },
  },
];

const DEFAULT: HelpEntry = {
  title: "FS-Platform",
  summary: "Prepare and review Ind AS financial statements with checks, controlled fixes, PDF reconciliation and a full audit trail.",
};

export function helpFor(pathname: string, mfaRequired = true): HelpEntry {
  const h = HELP.find((x) => x.match.test(pathname));
  if (!h) return DEFAULT;
  return (!mfaRequired && h.withoutMfa) || h.entry;
}
