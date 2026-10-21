import { describe, expect, it } from "vitest";

import type { MeLike } from "./permissions";
import { adminSections, can, hasNoRole, isClientUser, mfaRequired, navItems, needsMfa, permissionValue, roleCodes } from "./permissions";

function me(perms: Record<string, Record<string, string>>, firm = true, roles: string[] = []): MeLike {
  return {
    user: { id: "u1", is_firm_user: firm, email: "x@demo.fs.test", name: "X Y", mfa_enrolled_at: "2026-01-01T00:00:00Z" },
    permissions: perms,
    assignments: roles.map((r) => ({ role_code: r, client_id: "c1" })),
  };
}

const staff = me(
  {
    "*": { "engagement.view": "allow", "run.check": "allow", "fix.propose": "allow", "narrative.edit": "maker_checker", "rules.view": "allow", "file.download": "allow" },
    c1: { "engagement.view": "allow", "run.check": "allow", "fix.propose": "allow", "narrative.edit": "maker_checker" },
  },
  true,
  ["staff"],
);
const firmAdmin = me({ "*": { "users.manage": "allow", "client.manage": "allow", "audit.view": "allow", "admin.compliance": "allow", "ai.policy": "allow", "ops.manage": "allow", "rules.view": "allow" } }, true, ["firm_admin"]);
const client = me({ "*": { "portal.view": "allow", "engagement.view": "allow", "file.upload": "allow" }, c1: { "portal.view": "allow" } }, false, ["client_uploader"]);

describe("permissions", () => {
  it("reads client-scoped values before tenant-wide ones", () => {
    expect(permissionValue(staff, "narrative.edit", "c1")).toBe("maker_checker");
    expect(can(staff, "run.check", "c1")).toBe(true);
    expect(can(staff, "fix.approve", "c1")).toBe(false);
    expect(can(staff, "rules.view")).toBe(true);
    expect(can(null, "run.check")).toBe(false);
  });

  it("detects the pre-MFA state (no permissions yet)", () => {
    expect(needsMfa(me({}))).toBe(true);
    expect(needsMfa(staff)).toBe(false);
  });

  it("knows whether an authenticator is required (yes unless the server says no)", () => {
    expect(mfaRequired({ ...staff, mfa_required: false })).toBe(false);
    expect(mfaRequired({ ...staff, mfa_required: true })).toBe(true);
    expect(mfaRequired(staff)).toBe(true);
    expect(mfaRequired(null)).toBe(true);
  });

  it("spots a signed-in person with no role in force", () => {
    const now = new Date("2026-10-22T10:00:00Z");
    const newcomer = me({ "*": {} });
    expect(hasNoRole(newcomer, now)).toBe(true);
    expect(hasNoRole(staff, now)).toBe(false);
    expect(hasNoRole(firmAdmin, now)).toBe(false);
    const lapsed = { ...newcomer, assignments: [{ role_code: "staff", client_id: "c1", expires_at: "2026-10-21T10:00:00Z" }] };
    expect(hasNoRole(lapsed, now)).toBe(true);
    expect(hasNoRole({ ...lapsed, assignments: [{ role_code: "staff", client_id: "c1", expires_at: "2026-10-23T10:00:00Z" }] }, now)).toBe(false);
    expect(hasNoRole(null, now)).toBe(false);
  });

  it("lists roles and user type", () => {
    expect(roleCodes(staff)).toEqual(["staff"]);
    expect(isClientUser(client)).toBe(true);
    expect(isClientUser(staff)).toBe(false);
  });
});

describe("role-based navigation", () => {
  it("shows staff the work screens but not administration", () => {
    const labels = navItems(staff).map((n) => n.to);
    expect(labels).toEqual(expect.arrayContaining(["/", "/clients", "/rules", "/help"]));
    expect(labels).not.toContain("/admin");
    expect(labels).not.toContain("/portal");
    expect(labels).not.toContain("/audit");
  });

  it("shows the firm admin administration and the audit trail", () => {
    const labels = navItems(firmAdmin).map((n) => n.to);
    expect(labels).toEqual(expect.arrayContaining(["/", "/clients", "/audit", "/admin"]));
    expect(adminSections(firmAdmin).map((s) => s.key)).toEqual(
      expect.arrayContaining(["users", "roles", "access-reviews", "ai-policy", "incidents", "dsr", "evidence", "health"]),
    );
  });

  it("shows client users only the portal and help", () => {
    const labels = navItems(client).map((n) => n.to);
    expect(labels).toEqual(["/portal", "/help", "/release-notes"]);
    expect(adminSections(client)).toEqual([]);
  });

  it("gives someone without a role a way home and nothing else", () => {
    const newcomer = me({ "*": {} });
    expect(navItems(newcomer).map((n) => [n.to, n.label])).toEqual([
      ["/", "Home"],
      ["/help", "Help"],
      ["/release-notes", "Release notes"],
    ]);
  });

  it("gives a system admin operations sections only", () => {
    const sysadmin = me({ "*": { "ops.manage": "allow", "backup.manage": "allow", "rules.view": "allow" } }, true, ["system_admin"]);
    expect(adminSections(sysadmin).map((s) => s.key)).toEqual(["backups", "incidents", "releases", "health"]);
    expect(navItems(sysadmin).map((n) => n.to)).not.toContain("/clients");
  });
});
