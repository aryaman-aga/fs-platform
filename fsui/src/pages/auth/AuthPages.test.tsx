import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppRoutes } from "@/App";
import { ME, mockApi, renderWithProviders, reply } from "@/test/render";

import { AcceptInvitePage, ForgotPasswordPage, LoginPage } from "./AuthPages";

const SERVER = { setup_required: false, sso: false, edition: "server", self_service_reset: true };
const DESKTOP = { setup_required: false, sso: false, edition: "desktop", self_service_reset: false };

describe("Sign-in options", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("offers firm single sign-on only when it is configured", async () => {
    mockApi({ "/api/v1/auth/options": { ...SERVER, sso: true } });
    const { unmount } = renderWithProviders(<LoginPage />, { route: "/login" });
    expect(await screen.findByRole("link", { name: "Firm single sign-on" })).toHaveAttribute("href", "/api/v1/auth/oidc/login");
    unmount();
    vi.unstubAllGlobals();

    mockApi({ "/api/v1/auth/options": DESKTOP });
    renderWithProviders(<LoginPage />, { route: "/login" });
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("link", { name: "Forgot password?" })).toBeInTheDocument());
    expect(screen.queryByRole("link", { name: "Firm single sign-on" })).toBeNull();
  });

  it("without e-mail, forgot-password sends the user to an administrator", async () => {
    const { calls } = mockApi({ "/api/v1/auth/options": DESKTOP });
    renderWithProviders(<ForgotPasswordPage />, { route: "/forgot-password" });
    expect(await screen.findByText("Ask your administrator to reset your password.")).toBeInTheDocument();
    expect(screen.getByText(/it opens on this PC/)).toBeInTheDocument();
    expect(screen.queryByLabelText("E-mail")).toBeNull();
    expect(screen.queryByRole("button", { name: "Send reset link" })).toBeNull();
    expect(calls.some((c) => c.url.includes("/auth/password/forgot"))).toBe(false);
  });

  it("with e-mail, forgot-password sends a reset link", async () => {
    const { calls } = mockApi({ "/api/v1/auth/options": SERVER, "/api/v1/auth/password/forgot": { ok: true, message: "if the account exists, a reset link has been sent" } });
    renderWithProviders(<ForgotPasswordPage />, { route: "/forgot-password" });
    fireEvent.change(await screen.findByLabelText("E-mail"), { target: { value: "meera@corvus.test" } });
    fireEvent.click(screen.getByRole("button", { name: "Send reset link" }));
    expect(await screen.findByText("if the account exists, a reset link has been sent")).toBeInTheDocument();
    expect(calls.some((c) => c.url.endsWith("/auth/password/forgot") && c.init?.method === "POST")).toBe(true);
  });
});

describe("Forced password change", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("every screen leads to the change form until the one-time password is replaced", async () => {
    let changed = false;
    const me = () => ({ ...ME, must_change_password: !changed, user: { ...ME.user, must_change_password: !changed } });
    const { calls } = mockApi({
      "/api/v1/auth/options": SERVER,
      "/api/v1/auth/me": () => me(),
      "/api/v1/auth/password/change": () => {
        changed = true;
        return { ok: true, message: "password changed" };
      },
      "/api/v1/engagements": () => (changed ? [] : reply(403, { error: { code: "password_change_required", message: "change your one-time password" } })),
    });
    renderWithProviders(<AppRoutes />, { route: "/clients" });

    expect(await screen.findByText("Choose your own password")).toBeInTheDocument();
    expect(screen.getByText(/You signed in with a one-time password/)).toBeInTheDocument();
    const submit = screen.getByRole("button", { name: "Change password" });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText("One-time password"), { target: { value: "AbCde-FgHij-KmNpq-RsTuv" } });
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "Harbour-Ledger-Spruce-41" } });
    fireEvent.change(screen.getByLabelText("Repeat new password"), { target: { value: "Harbour-Ledger-Spruce-41" } });
    await waitFor(() => expect(submit).toBeEnabled());
    fireEvent.click(submit);

    await waitFor(() => expect(screen.queryByText("Choose your own password")).toBeNull());
    const post = calls.find((c) => c.url.endsWith("/auth/password/change"));
    expect(JSON.parse(String(post?.init?.body))).toEqual({ current_password: "AbCde-FgHij-KmNpq-RsTuv", new_password: "Harbour-Ledger-Spruce-41" });
    expect(await screen.findByText(/^Good (morning|afternoon|evening)/)).toBeInTheDocument(); // the dashboard
  });

  it("a refusal from the server sends the user to the change form", async () => {
    let refused = false;
    mockApi({
      "/api/v1/auth/options": SERVER,
      // the session's flag is only learnt from the refusal (e.g. the page was open before the sign-in)
      "/api/v1/auth/me": () => (refused ? { ...ME, must_change_password: true, user: { ...ME.user, must_change_password: true } } : ME),
      "/api/v1/engagements": () => {
        refused = true;
        return reply(403, { error: { code: "password_change_required", message: "change your one-time password" } });
      },
    });
    renderWithProviders(<AppRoutes />, { route: "/" });
    expect(await screen.findByText("Choose your own password")).toBeInTheDocument();
  });
});

describe("Links handed over on a shared PC", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("setting a colleague's password signs out whoever is signed in on this browser", async () => {
    const { calls } = mockApi({
      "/api/v1/auth/options": DESKTOP,
      "/api/v1/auth/me": ME,
      "/api/v1/auth/invite/accept": { ok: true, message: "password set; sign in and enrol your authenticator" },
      "/api/v1/auth/logout": { ok: true, message: "signed out" },
    });
    renderWithProviders(<AcceptInvitePage />, { route: "/invite?token=inv-123" });
    expect(await screen.findByText(/staff@demo.fs.test is signed in on this browser/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "Harbour-Ledger-Spruce-41" } });
    fireEvent.change(screen.getByLabelText("Repeat password"), { target: { value: "Harbour-Ledger-Spruce-41" } });
    fireEvent.click(screen.getByRole("button", { name: "Set password" }));
    expect(await screen.findByText("password set; sign in and enrol your authenticator")).toBeInTheDocument();
    const accept = calls.find((c) => c.url.endsWith("/auth/invite/accept"));
    expect(JSON.parse(String(accept?.init?.body))).toEqual({ token: "inv-123", password: "Harbour-Ledger-Spruce-41" });
    expect(calls.some((c) => c.url.endsWith("/auth/logout") && c.init?.method === "POST")).toBe(true);
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
  });
});
