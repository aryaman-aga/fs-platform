/** Finding filters, ordering and counts for the Checks tab. */

export const SEVERITIES = ["High", "Medium", "Low", "Info"] as const;
export type Severity = (typeof SEVERITIES)[number];
export const FINDING_STATUSES = ["Open", "Accepted", "Not an issue", "Resolved"] as const;

export interface FindingLike {
  id: string;
  fid: string;
  rule_id: string;
  family?: string;
  severity: string;
  sheet: string | null;
  cells: unknown[];
  caption: string | null;
  explanation: string;
  status: string;
  assigned_to?: string | null;
}

export interface FindingFilter {
  severity?: string[];
  rule?: string;
  sheet?: string;
  status?: string[];
  text?: string;
  assignedTo?: string;
}

const SEV_RANK: Record<string, number> = { High: 0, Medium: 1, Low: 2, Info: 3 };

export function severityRank(sev: string): number {
  return SEV_RANK[sev] ?? 9;
}

export function filterFindings<T extends FindingLike>(items: T[], f: FindingFilter): T[] {
  const text = (f.text ?? "").trim().toLowerCase();
  const rule = (f.rule ?? "").trim().toUpperCase();
  return items.filter((x) => {
    if (f.severity && f.severity.length && !f.severity.includes(x.severity)) return false;
    if (f.status && f.status.length && !f.status.includes(x.status)) return false;
    if (rule && !x.rule_id.toUpperCase().startsWith(rule)) return false;
    if (f.sheet && x.sheet !== f.sheet) return false;
    if (f.assignedTo && x.assigned_to !== f.assignedTo) return false;
    if (text) {
      const hay = [x.fid, x.rule_id, x.sheet ?? "", x.caption ?? "", x.explanation, ...x.cells.map(String)].join(" ").toLowerCase();
      if (!hay.includes(text)) return false;
    }
    return true;
  });
}

export function sortFindings<T extends FindingLike>(items: T[]): T[] {
  return [...items].sort(
    (a, b) => severityRank(a.severity) - severityRank(b.severity) || a.fid.localeCompare(b.fid, "en", { numeric: true }),
  );
}

export function countBySeverity(items: FindingLike[]): Record<Severity, number> {
  const out: Record<Severity, number> = { High: 0, Medium: 0, Low: 0, Info: 0 };
  for (const x of items) {
    if (x.severity in out) out[x.severity as Severity] += 1;
  }
  return out;
}

export function distinctSheets(items: FindingLike[]): string[] {
  return [...new Set(items.map((x) => x.sheet).filter((s): s is string => !!s))].sort();
}

export function distinctRuleFamilies(items: FindingLike[]): string[] {
  return [...new Set(items.map((x) => x.rule_id.split("-")[0] ?? x.rule_id))].sort();
}

/** "Not an issue" needs a reason (E-RULE-05 rule-quality analytics). */
export function statusChangeError(status: string, reason: string): string | null {
  if (status === "Not an issue" && !reason.trim()) return "Give a reason: it feeds the rule-quality analytics.";
  return null;
}
