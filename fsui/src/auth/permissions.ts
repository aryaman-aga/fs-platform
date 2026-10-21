/**
 * Role-aware UI decisions from /auth/me.permissions
 * (client id, or "*" for tenant-wide → permission → value).
 *
 * Values follow the 05 §3 matrix: allow, maker_checker, not_own, shared_only,
 * role_setting, policy, own_clients, dual, approve_restore. Absent = denied.
 * Hiding a button is a convenience only — the server checks every request.
 */

export type PermissionMap = Record<string, Record<string, string>>;

export interface MeLike {
  user: { id: string; is_firm_user: boolean; email: string; name: string; mfa_enrolled_at?: string | null; must_change_password?: boolean };
  permissions: PermissionMap;
  assignments: { role_code: string; client_id: string | null; client_name?: string | null; expires_at?: string | null }[];
  must_change_password?: boolean;
  mfa_required?: boolean;
}

/** Signed in with a one-time password: the server serves nothing else until it is changed. */
export function mustChangePassword(me: MeLike | null | undefined): boolean {
  return !!me && !!(me.must_change_password || me.user.must_change_password);
}

/** False where the installation signs in with e-mail and password only (no authenticator app), as /auth/me or
 * /auth/options say; anything else (an older server, not loaded yet) keeps the authenticator. */
export function mfaRequired(x: { mfa_required?: boolean } | null | undefined): boolean {
  return x?.mfa_required !== false;
}

/** A role assignment that grants something now (time-boxed ones lapse at `expires_at`). */
export function roleInForce(a: { expires_at?: string | null }, now: Date = new Date()): boolean {
  return !a.expires_at || new Date(a.expires_at).getTime() > now.getTime();
}

/** Signed in, but no role in force anywhere (so no Firm Admin either), e.g. a self-service account: it sees no client
 * until an administrator assigns one. */
export function hasNoRole(me: MeLike | null | undefined, now: Date = new Date()): boolean {
  return !!me && !me.assignments.some((a) => roleInForce(a, now));
}

export function permissionValue(me: MeLike | null | undefined, permission: string, clientId?: string | null): string | null {
  if (!me) return null;
  const scoped = clientId ? me.permissions[clientId] : undefined;
  const v = scoped?.[permission] ?? me.permissions["*"]?.[permission];
  return v && v !== "deny" ? v : null;
}

export function can(me: MeLike | null | undefined, permission: string, clientId?: string | null): boolean {
  return permissionValue(me, permission, clientId) !== null;
}

export function canAny(me: MeLike | null | undefined, permissions: string[], clientId?: string | null): boolean {
  return permissions.some((p) => can(me, p, clientId));
}

export function roleCodes(me: MeLike | null | undefined): string[] {
  return [...new Set((me?.assignments ?? []).map((a) => a.role_code))].sort();
}

export function isClientUser(me: MeLike | null | undefined): boolean {
  return !!me && !me.user.is_firm_user;
}

/** Signed in with the password but the second factor is not yet verified. */
export function needsMfa(me: MeLike | null | undefined): boolean {
  return !!me && Object.keys(me.permissions ?? {}).length === 0;
}

export interface NavItem {
  to: string;
  label: string;
  icon: string;
  section: "work" | "admin" | "help";
}

/** Navigation for the signed-in user (08 §3 screens 2–6). */
export function navItems(me: MeLike | null | undefined): NavItem[] {
  if (!me) return [];
  const out: NavItem[] = [];
  if (isClientUser(me)) {
    if (can(me, "portal.view")) out.push({ to: "/portal", label: "Client portal", icon: "portal", section: "work" });
  } else {
    if (can(me, "engagement.view") || can(me, "client.manage")) out.push({ to: "/", label: "Dashboard", icon: "home", section: "work" });
    else if (hasNoRole(me)) out.push({ to: "/", label: "Home", icon: "home", section: "work" }); // explains how to get access
    if (can(me, "engagement.view") || can(me, "client.manage")) out.push({ to: "/clients", label: "Clients & entities", icon: "clients", section: "work" });
    if (can(me, "audit.view")) out.push({ to: "/audit", label: "Audit trail", icon: "audit", section: "work" });
    if (can(me, "rules.view")) out.push({ to: "/rules", label: "Rule registry", icon: "rules", section: "work" });
    if (canAny(me, ["users.manage", "admin.compliance", "ops.manage", "ai.policy", "backup.manage"])) {
      out.push({ to: "/admin", label: "Administration", icon: "admin", section: "admin" });
    }
  }
  out.push({ to: "/help", label: "Help", icon: "help", section: "help" });
  out.push({ to: "/release-notes", label: "Release notes", icon: "notes", section: "help" });
  return out;
}

export interface AdminSection {
  key: string;
  label: string;
  permissions: string[];
}

// each section is gated by the permission its list endpoint checks (fsweb routers/admin.py, compliance.py)
export const ADMIN_SECTIONS: AdminSection[] = [
  { key: "users", label: "Users", permissions: ["users.manage"] },
  { key: "firm", label: "Firm settings", permissions: ["users.manage"] },
  { key: "roles", label: "Roles & permissions", permissions: ["users.manage"] },
  { key: "assignments", label: "Assignments", permissions: ["users.manage"] },
  { key: "elevations", label: "Just-in-time elevation", permissions: ["users.manage"] },
  { key: "access-reviews", label: "Access reviews", permissions: ["admin.compliance"] },
  { key: "ai-policy", label: "AI policy", permissions: ["ai.policy"] },
  { key: "abac", label: "Access policies (ABAC)", permissions: ["users.manage"] },
  { key: "backups", label: "Backups & restore", permissions: ["backup.manage"] },
  { key: "incidents", label: "Incidents (CERT-In)", permissions: ["ops.manage"] },
  { key: "dsr", label: "Data-principal requests (DPDP)", permissions: ["admin.compliance"] },
  { key: "releases", label: "Releases (change log)", permissions: ["ops.manage"] },
  { key: "usage", label: "Usage & billing", permissions: ["admin.compliance"] },
  { key: "health", label: "System health", permissions: ["ops.manage"] },
  { key: "evidence", label: "Evidence pack", permissions: ["admin.compliance"] },
];

export function adminSections(me: MeLike | null | undefined): AdminSection[] {
  return ADMIN_SECTIONS.filter((s) => canAny(me, s.permissions));
}
