import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppRoutes } from "@/App";
import { ME, mockApi, renderWithProviders } from "@/test/render";

const OPTIONS = { setup_required: false, sso: false, edition: "desktop", self_service_reset: false, mfa_required: false, signup: true };
// e.g. a self-service account: signed in, nobody has given it a role yet
const NO_ROLE = { ...ME, mfa_required: false, assignments: [], permissions: { "*": {} } };
const ADMIN = {
  ...ME,
  mfa_required: false,
  assignments: [{ id: "a0", user_id: "u-staff", client_id: null, client_name: null, role_code: "firm_admin", role_name: "Firm Admin", created_at: "2026-01-01T00:00:00Z" }],
  permissions: { "*": { "client.manage": "allow", "users.manage": "allow" } },
};
const MESSAGE = "You're signed in, but you don't have access to any clients yet. Ask your FS-Platform administrator to give you a role.";

describe("Signed in without a role", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("home explains that an administrator gives access, and shows the new role once given", async () => {
    let assigned = false;
    mockApi({ "/api/v1/auth/options": OPTIONS, "/api/v1/auth/me": () => (assigned ? ME : NO_ROLE), "/api/v1/engagements": [] });
    renderWithProviders(<AppRoutes />, { route: "/" });

    expect(await screen.findByText(MESSAGE)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Welcome, Sunil" })).toBeInTheDocument();
    expect(screen.getByTestId("no-role")).toHaveTextContent("Tell them the e-mail address you signed in with: staff@demo.fs.test");
    expect(within(screen.getByRole("navigation", { name: "Main" })).getByRole("link", { name: "Home" })).toHaveAttribute("href", "/");

    assigned = true; // the administrator gives a role meanwhile
    fireEvent.click(screen.getByRole("button", { name: "Check again" }));
    expect(await screen.findByText(/^Good (morning|afternoon|evening)/)).toBeInTheDocument();
    expect(screen.queryByText(MESSAGE)).toBeNull();
  });

  it("the client list explains it too", async () => {
    mockApi({ "/api/v1/auth/options": OPTIONS, "/api/v1/auth/me": NO_ROLE, "/api/v1/clients": [] });
    renderWithProviders(<AppRoutes />, { route: "/clients" });
    expect(await screen.findByText(MESSAGE)).toBeInTheDocument();
    expect(screen.queryByText("No clients yet.")).toBeNull();
  });

  it("an administrator with no clients yet sees the usual empty lists", async () => {
    mockApi({ "/api/v1/auth/options": OPTIONS, "/api/v1/auth/me": ADMIN, "/api/v1/clients": [], "/api/v1/engagements": [] });
    const { unmount } = renderWithProviders(<AppRoutes />, { route: "/clients" });
    expect(await screen.findByText("No clients yet.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New client" })).toBeInTheDocument();
    expect(screen.queryByText(MESSAGE)).toBeNull();
    unmount();

    renderWithProviders(<AppRoutes />, { route: "/" });
    expect(await screen.findByText("No engagements are assigned to you yet.")).toBeInTheDocument();
    expect(screen.queryByText(MESSAGE)).toBeNull();
  });
});
