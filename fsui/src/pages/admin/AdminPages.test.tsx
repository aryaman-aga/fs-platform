import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ME, mockApi, renderWithProviders } from "@/test/render";

import { AdminPage } from "./AdminPages";

const ADMIN_ME = {
  ...ME,
  user: { ...ME.user, id: "u-admin", email: "asha@corvus.test", name: "Asha Rao" },
  session: { ...ME.session, stepup_valid_until: "2099-01-01T00:00:00Z" }, // a fresh step-up: only a reason is asked
  assignments: [{ id: "a0", user_id: "u-admin", client_id: null, client_name: null, role_code: "firm_admin", role_name: "Firm Admin", created_at: "2026-01-01T00:00:00Z" }],
  permissions: { "*": { "users.manage": "allow", "admin.compliance": "allow", "ops.manage": "allow", "backup.manage": "approve_restore" } },
};
const DESKTOP = { setup_required: false, sso: false, edition: "desktop", self_service_reset: false };
const MEERA = { id: "u-meera", email: "meera@corvus.test", name: "Meera Pillai", status: "active", is_firm_user: true, mfa_type: "totp",
  mfa_enrolled_at: "2026-10-01T00:00:00Z", last_login_at: null, must_change_password: false, created_at: "2026-10-01T00:00:00Z" };

function renderAdmin(section: string) {
  return renderWithProviders(
    <Routes>
      <Route path="/admin/:section" element={<AdminPage />} />
    </Routes>,
    { route: `/admin/${section}` },
  );
}

function header(init: RequestInit | undefined, name: string): string | null {
  return new Headers(init?.headers).get(name);
}

describe("Administration on the desktop edition", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("Reset password issues a one-time link to hand over", async () => {
    const { calls } = mockApi({
      "/api/v1/auth/me": ADMIN_ME,
      "/api/v1/auth/options": DESKTOP,
      "/api/v1/admin/users": [MEERA],
      "/api/v1/admin/roles": [],
      "/api/v1/clients": [],
      "/api/v1/admin/users/u-meera/password-reset-link": { link: "http://127.0.0.1:8700/reset-password?token=r3s3t", expires_at: "2026-10-22T10:00:00Z" },
    });
    renderAdmin("users");
    const row = (await screen.findByText("meera@corvus.test")).closest("tr") as HTMLElement;
    expect(within(row).getByRole("button", { name: "Reset MFA" })).toBeInTheDocument();
    fireEvent.click(within(row).getByRole("button", { name: "Reset password" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Reset password for meera@corvus.test")).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("Reason (recorded in the audit trail)"), { target: { value: "forgot her password" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirm" }));

    const notice = await screen.findByTestId("reset-link");
    expect(notice).toHaveTextContent("Password reset for Meera Pillai: give them this link — it opens on this PC; valid until");
    expect(notice).toHaveTextContent("http://127.0.0.1:8700/reset-password?token=r3s3t");
    const post = calls.find((c) => c.url.endsWith("/admin/users/u-meera/password-reset-link"));
    expect(post?.init?.method).toBe("POST");
    expect(header(post?.init, "X-Action-Reason")).toBe("forgot her password");
    expect(calls.some((c) => c.url.endsWith("/auth/step-up"))).toBe(false);
  });

  it("an invitation shows the link to give to the colleague", async () => {
    mockApi({
      "/api/v1/auth/me": ADMIN_ME,
      "/api/v1/auth/options": DESKTOP,
      "/api/v1/admin/users": [MEERA],
      "/api/v1/admin/roles": [{ id: "r1", code: "firm_admin", name: "Firm Admin", scope: "tenant", is_system: true, is_client_role: false, download_allowed: false, description: "" }],
      "/api/v1/clients": [],
      "/api/v1/admin/users/invite": { user: { ...MEERA, id: "u-ravi", email: "ravi@corvus.test", status: "invited" }, invite_link: "http://127.0.0.1:8700/invite?token=inv-1", expires_at: "2026-10-24T10:00:00Z" },
    });
    renderAdmin("users");
    fireEvent.click(await screen.findByRole("button", { name: "Invite user" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("E-mail"), { target: { value: "ravi@corvus.test" } });
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "Ravi Iyer" } });
    fireEvent.change(within(dialog).getByLabelText("Role"), { target: { value: "firm_admin" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Send invitation" }));
    const notice = await screen.findByTestId("invite-link");
    expect(notice).toHaveTextContent("Give this link to your colleague — it opens on this PC; valid until");
    expect(notice).toHaveTextContent("http://127.0.0.1:8700/invite?token=inv-1");
  });

  it("the Backups card sets the backup copy folder and lists desktop backups", async () => {
    const { calls } = mockApi({
      "/api/v1/auth/me": ADMIN_ME,
      "/api/v1/auth/options": DESKTOP,
      "/api/v1/admin/backups": [
        { id: "b1", kind: "desktop", started: "2026-10-21T02:00:00Z", finished: "2026-10-21T02:01:00Z", status: "ok", size: 3 * 1024 ** 3, verified: true,
          location: "C:\\Users\\asha\\AppData\\Local\\FSPlatform\\offline\\backups\\fs-2026-10-21.zip",
          details_json: { copied_to: "D:\\FS-Platform backups\\fs-2026-10-21.zip" } },
        { id: "b2", kind: "desktop", started: "2026-10-20T02:00:00Z", finished: null, status: "failed", size: 0, verified: false, location: "", details_json: { copy_error: "D: not found" } },
      ],
      "/api/v1/admin/backup-settings": (init?: RequestInit) =>
        init?.method === "PUT" ? { backup_copy_dir: "D:\\FS-Platform backups", exists: true } : { backup_copy_dir: null, exists: null },
    });
    renderAdmin("backups");
    const grid = await screen.findByRole("table", { name: "Backup log" });
    await waitFor(() => expect(within(grid).getAllByText("Desktop backup")).toHaveLength(2));
    expect(within(grid).getByText("copy: D:\\FS-Platform backups\\fs-2026-10-21.zip")).toBeInTheDocument();
    expect(within(grid).getByText("3.00 GB")).toBeInTheDocument();
    expect(within(grid).getByText("copy failed")).toBeInTheDocument();

    const card = screen.getByTestId("backup-copy-folder");
    expect(within(card).getByText("Desktop edition: every backup is also copied here")).toBeInTheDocument();
    const input = within(card).getByLabelText("Backup copy folder");
    const save = within(card).getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    fireEvent.change(input, { target: { value: "D:\\FS-Platform backups" } });
    fireEvent.click(save);
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Reason (recorded in the audit trail)"), { target: { value: "second copy on D" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/admin/backup-settings") && c.init?.method === "PUT")).toBe(true));
    const put = calls.find((c) => c.url.endsWith("/admin/backup-settings") && c.init?.method === "PUT");
    expect(JSON.parse(String(put?.init?.body))).toEqual({ backup_copy_dir: "D:\\FS-Platform backups" });
    expect(header(put?.init, "X-Action-Reason")).toBe("second copy on D");
  });

  it("access reviews flag a Firm Admin auto-assigned as Partner", async () => {
    mockApi({
      "/api/v1/auth/me": ADMIN_ME,
      "/api/v1/auth/options": DESKTOP,
      "/api/v1/admin/users": [MEERA],
      "/api/v1/admin/access-reviews": [
        { id: "i1", period: "2026Q4", client_id: "c1", reviewer_id: null, user_id: "u-admin", role_id: "r-partner", assignment_id: "a1", decision: "pending",
          decided_at: null, decided_by: null, due_at: "2026-11-04T00:00:00Z", sod_warning: "Firm Admin made Partner automatically when creating this client" },
        { id: "i2", period: "2026Q4", client_id: "c1", reviewer_id: null, user_id: "u-meera", role_id: "r-staff", assignment_id: "a2", decision: "pending",
          decided_at: null, decided_by: null, due_at: "2026-11-04T00:00:00Z", sod_warning: null },
      ],
    });
    renderAdmin("access-reviews");
    const grid = await screen.findByRole("table", { name: "Access review items" });
    expect(await within(grid).findByText(/Firm Admin made Partner automatically/)).toBeInTheDocument();
    expect(within(grid).getAllByText(/made Partner automatically/)).toHaveLength(1);
  });

  it("with an authenticator, Users shows MFA and the reset link keeps the authenticator enrolled", async () => {
    mockApi({
      "/api/v1/auth/me": { ...ADMIN_ME, session: { ...ADMIN_ME.session, stepup_valid_until: null } },
      "/api/v1/auth/options": DESKTOP,
      "/api/v1/admin/users": [MEERA],
      "/api/v1/admin/roles": [],
      "/api/v1/clients": [],
    });
    renderAdmin("users");
    const row = (await screen.findByText("meera@corvus.test")).closest("tr") as HTMLElement;
    expect(screen.getByRole("columnheader", { name: "MFA" })).toBeInTheDocument();
    fireEvent.click(within(row).getByRole("button", { name: "Reset password" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/their authenticator stays enrolled/)).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Authenticator code")).toBeInTheDocument();
  });

  it("Firm settings switch the administrator's engagement work on, with step-up and a reason", async () => {
    const { calls } = mockApi({
      "/api/v1/auth/me": ADMIN_ME,
      "/api/v1/auth/options": DESKTOP,
      "/api/v1/admin/tenant-settings": (init?: RequestInit) => ({ works_on_engagements: init?.method === "PUT" }),
    });
    renderAdmin("firm");
    const box = await screen.findByLabelText("Firm Admins also work on engagements");
    expect(box).not.toBeChecked();
    fireEvent.click(box);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Reason (recorded in the audit trail)"), { target: { value: "I am also the partner" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/admin/tenant-settings") && c.init?.method === "PUT")).toBe(true));
    const put = calls.find((c) => c.init?.method === "PUT");
    expect(JSON.parse(String(put?.init?.body))).toEqual({ works_on_engagements: true });
  });
});

describe("Administration without an authenticator, with self-service accounts", () => {
  afterEach(() => vi.unstubAllGlobals());

  const ADMIN = { ...ADMIN_ME, mfa_required: false, permissions: { "*": { ...ADMIN_ME.permissions["*"], "engagement.assign": "allow" } } };
  const OPTIONS = { ...DESKTOP, mfa_required: false, signup: true };
  // created their own account yesterday: no role yet
  const RAVI = { ...MEERA, id: "u-ravi", email: "ravi@corvus.test", name: "Ravi Iyer", mfa_type: "none", mfa_enrolled_at: null, created_at: "2026-10-21T00:00:00Z" };
  const MEERA_STAFF = { id: "a2", user_id: "u-meera", client_id: "c1", client_name: "Synthetic Demo", role_code: "staff", role_name: "Staff / Article", created_at: "2026-10-01T00:00:00Z" };

  it("Users marks people with no role yet and leads to Assignments; nothing about MFA", async () => {
    mockApi({
      "/api/v1/auth/me": ADMIN,
      "/api/v1/auth/options": OPTIONS,
      "/api/v1/admin/users": [MEERA, RAVI],
      "/api/v1/admin/roles": [],
      "/api/v1/clients": [],
      "/api/v1/admin/assignments": [MEERA_STAFF],
    });
    renderAdmin("users");
    const ravi = (await screen.findByText("ravi@corvus.test")).closest("tr") as HTMLElement;
    expect(await within(ravi).findByText("No role yet")).toBeInTheDocument();
    expect(within(ravi).getByRole("link", { name: "Give a role" })).toHaveAttribute("href", "/admin/assignments?user=u-ravi");
    const meera = screen.getByText("meera@corvus.test").closest("tr") as HTMLElement;
    expect(within(meera).queryByText("No role yet")).toBeNull();
    expect(within(meera).queryByRole("link", { name: "Give a role" })).toBeNull();
    const hint = screen.getByTestId("no-role-hint");
    expect(hint).toHaveTextContent("Ravi Iyer has no role yet, so they cannot see any client.");
    expect(within(hint).getByRole("link", { name: "Give them a role under Assignments" })).toHaveAttribute("href", "/admin/assignments?user=u-ravi");

    expect(screen.queryByRole("columnheader", { name: "MFA" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reset MFA" })).toBeNull();
    fireEvent.click(within(ravi).getByRole("button", { name: "Reset password" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).not.toHaveTextContent(/authenticator|MFA/);
  });

  it("Give a role opens Assignments with the person already chosen; a per-client role needs its client", async () => {
    const { calls } = mockApi({
      "/api/v1/auth/me": ADMIN,
      "/api/v1/auth/options": OPTIONS,
      "/api/v1/admin/users": [MEERA, RAVI],
      "/api/v1/admin/roles": [
        { id: "r-staff", code: "staff", name: "Staff / Article", scope: "client", is_system: true, is_client_role: false, download_allowed: true, description: "" },
        { id: "r-fa", code: "firm_admin", name: "Firm Admin", scope: "tenant", is_system: true, is_client_role: false, download_allowed: false, description: "" },
      ],
      "/api/v1/clients": [{ id: "c1", name: "Synthetic Demo", classification: "confidential", status: "active" }],
      "/api/v1/admin/assignments": (init?: RequestInit) => (init?.method === "POST" ? { ...MEERA_STAFF, id: "a3", user_id: "u-ravi" } : [MEERA_STAFF]),
    });
    renderAdmin("assignments?user=u-ravi");
    await screen.findByRole("option", { name: "ravi@corvus.test" });
    expect(screen.getByLabelText("User")).toHaveValue("u-ravi");
    await screen.findByRole("option", { name: "Staff / Article" });
    const assign = screen.getByRole("button", { name: "Assign" });
    expect(assign).toBeDisabled(); // Staff is per client: choose the client first
    const card = screen.getByRole("heading", { name: "Assign a role" }).closest("section") as HTMLElement;
    fireEvent.change(within(card).getByLabelText("Client"), { target: { value: "c1" } });
    expect(assign).toBeEnabled();
    fireEvent.click(assign);
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Reason (recorded in the audit trail)"), { target: { value: "joined the audit team" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/admin/assignments") && c.init?.method === "POST")).toBe(true));
    const post = calls.find((c) => c.url.endsWith("/admin/assignments") && c.init?.method === "POST");
    expect(JSON.parse(String(post?.init?.body))).toEqual({ user_id: "u-ravi", role_code: "staff", client_id: "c1" });
    expect(header(post?.init, "X-Action-Reason")).toBe("joined the audit team");
  });

  it("the role list offers only the roles that fit the chosen person", async () => {
    mockApi({
      "/api/v1/auth/me": ADMIN,
      "/api/v1/auth/options": OPTIONS,
      "/api/v1/admin/users": [MEERA, RAVI],
      "/api/v1/admin/roles": [
        { id: "r-staff", code: "staff", name: "Staff / Article", scope: "client", is_system: true, is_client_role: false, download_allowed: true, description: "" },
        { id: "r-cv", code: "client_viewer", name: "Client – Viewer", scope: "client", is_system: true, is_client_role: true, download_allowed: false, description: "" },
      ],
      "/api/v1/clients": [{ id: "c1", name: "Synthetic Demo", classification: "confidential", status: "active" }],
      "/api/v1/admin/assignments": [MEERA_STAFF],
    });
    renderAdmin("assignments?user=u-ravi");
    await screen.findByRole("option", { name: "Staff / Article" });
    expect(screen.queryByRole("option", { name: "Client – Viewer" })).toBeNull(); // Ravi is a firm user
  });
});
