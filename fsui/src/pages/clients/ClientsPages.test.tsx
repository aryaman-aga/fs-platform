import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppRoutes } from "@/App";
import { ME, mockApi, renderWithProviders } from "@/test/render";

const OPTIONS = { setup_required: false, sso: false, edition: "desktop", self_service_reset: false, mfa_required: false, signup: true };
const ADMIN = {
  ...ME,
  mfa_required: false,
  assignments: [{ id: "a0", user_id: "u-staff", client_id: null, client_name: null, role_code: "firm_admin", role_name: "Firm Admin", created_at: "2026-01-01T00:00:00Z" }],
  permissions: { "*": { "client.manage": "allow", "users.manage": "allow" } },
};
const CLIENT = { id: "c9", name: "Synthetic Traders", classification: "confidential", status: "active" };

describe("New client", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("reloads the creator's permissions before opening the client (they may now be its Partner)", async () => {
    const { calls } = mockApi({
      "/api/v1/auth/options": OPTIONS,
      "/api/v1/auth/me": ADMIN,
      "/api/v1/clients": (init?: RequestInit) => (init?.method === "POST" ? CLIENT : []),
      "/api/v1/clients/c9": CLIENT,
      "/api/v1/clients/c9/entities": [],
      "/api/v1/engagements": [],
    });
    renderWithProviders(<AppRoutes />, { route: "/clients" });
    fireEvent.click(await screen.findByRole("button", { name: "New client" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText(/^Name/), { target: { value: "Synthetic Traders" } });
    const before = calls.filter((c) => c.url.endsWith("/auth/me")).length;
    fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/api/v1/clients") && c.init?.method === "POST")).toBe(true));
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/auth/me")).length).toBeGreaterThan(before));
  });
});
