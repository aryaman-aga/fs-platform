import { fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppRoutes } from "@/App";
import { ME, mockApi, renderWithProviders, reply } from "@/test/render";

// the desktop edition with e-mail and password only, and "Create an account"
const OPTIONS = { setup_required: false, sso: false, edition: "desktop", self_service_reset: false, mfa_required: false, signup: true };
const SIGNED_OUT = () => reply(401, { error: { code: "unauthenticated", message: "sign in first" } });
const CREATED = { csrf_token: "csrf-1", expires_at: "2026-10-22T12:00:00Z" };
// the new account: signed in, but no role yet
const RAVI = {
  ...ME,
  mfa_required: false,
  user: { ...ME.user, id: "u-ravi", email: "ravi@corvus.test", name: "Ravi Iyer", mfa_enrolled_at: null },
  assignments: [],
  permissions: { "*": {} },
};
const NO_ROLE = "You're signed in, but you don't have access to any clients yet. Ask your FS-Platform administrator to give you a role.";

function fill(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

function fillForm(password = "Blue tea cups at noon", confirm = password) {
  fill("Full name", "Ravi Iyer");
  fill("E-mail", "ravi@corvus.test");
  fill("Password", password);
  fill("Confirm password", confirm);
}

async function openSignup() {
  renderWithProviders(<AppRoutes />, { route: "/signup" });
  return screen.findByRole("form", { name: "Create your account" });
}

describe("Create an account", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("creates the account, signs in and explains that an administrator gives access", async () => {
    let created = false;
    const { calls } = mockApi({
      "/api/v1/auth/options": OPTIONS,
      "/api/v1/auth/me": () => (created ? RAVI : SIGNED_OUT()),
      "/api/v1/auth/signup": () => {
        created = true;
        return reply(201, { ...CREATED, status: "ok" });
      },
      "/api/v1/engagements": [],
    });
    await openSignup();
    expect(screen.getByLabelText("Full name")).toHaveAttribute("autocomplete", "name");
    expect(screen.getByLabelText("E-mail")).toHaveAttribute("autocomplete", "email");
    expect(screen.getByLabelText("Password")).toHaveAttribute("autocomplete", "new-password");
    expect(screen.getByLabelText("Confirm password")).toHaveAttribute("autocomplete", "new-password");
    expect(screen.getByText("At least 12 characters — a short sentence works well, e.g. ‘Blue tea cups at noon’")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/authenticator|QR|code/i);
    fillForm();
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByText(NO_ROLE)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Welcome, Ravi" })).toBeInTheDocument();
    const post = calls.find((c) => c.url.endsWith("/api/v1/auth/signup"));
    expect(post?.init?.method).toBe("POST");
    expect(JSON.parse(String(post?.init?.body))).toEqual({ name: "Ravi Iyer", email: "ravi@corvus.test", password: "Blue tea cups at noon" });
  });

  it("points out a password that is too short or does not match before sending anything", async () => {
    const { calls } = mockApi({ "/api/v1/auth/options": OPTIONS, "/api/v1/auth/me": SIGNED_OUT });
    await openSignup();
    fillForm("Blue tea cups at noon", "Blue tea");
    expect(screen.queryByText("The passwords don't match.")).toBeNull(); // still typing
    fill("Confirm password", "Blue tea cups at night");
    expect(screen.getByText("The passwords don't match.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));

    fill("Password", "Blue tea");
    fill("Confirm password", "Blue tea");
    expect(screen.queryByText("The passwords don't match.")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(screen.getByText("Use at least 12 characters.")).toBeInTheDocument();
    expect(calls.some((c) => c.url.endsWith("/auth/signup"))).toBe(false);
  });

  it("an e-mail that already has an account offers to sign in instead, with the e-mail filled in", async () => {
    mockApi({
      "/api/v1/auth/options": OPTIONS,
      "/api/v1/auth/me": SIGNED_OUT,
      "/api/v1/auth/signup": () =>
        reply(409, { error: { code: "email_taken", message: "an account with this e-mail already exists: sign in, or ask an administrator to reset its password" } }),
    });
    await openSignup();
    fillForm();
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByText(/There is already an account with this e-mail address/)).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "Sign in instead" });
    expect(link).toHaveAttribute("href", "/login");
    fireEvent.click(link);
    expect(await screen.findByRole("form", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByLabelText("E-mail")).toHaveValue("ravi@corvus.test");
  });

  it("a password the server refuses is explained under the field", async () => {
    mockApi({
      "/api/v1/auth/options": OPTIONS,
      "/api/v1/auth/me": SIGNED_OUT,
      "/api/v1/auth/signup": () => reply(400, { error: { code: "password_policy", message: "password must not contain your name" } }),
    });
    await openSignup();
    fillForm("Ravi Iyer at noon tea");
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByText("Password must not contain your name.")).toBeInTheDocument();
    fill("Password", "Blue tea cups at noon");
    expect(screen.queryByText("Password must not contain your name.")).toBeNull();
  });

  it("where accounts are made by an administrator, says so", async () => {
    mockApi({
      "/api/v1/auth/options": OPTIONS,
      "/api/v1/auth/me": SIGNED_OUT,
      "/api/v1/auth/signup": () => reply(403, { error: { code: "signup_disabled", message: "accounts are created by an administrator on this installation" } }),
    });
    await openSignup();
    fillForm();
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByText(/Ask your administrator to create your account\./)).toBeInTheDocument();
  });

  it("without sign-up, the page leads to the sign-in page", async () => {
    mockApi({ "/api/v1/auth/options": { ...OPTIONS, signup: false }, "/api/v1/auth/me": SIGNED_OUT });
    renderWithProviders(<AppRoutes />, { route: "/signup" });
    expect(await screen.findByRole("form", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Create your account" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Create an account" })).toBeNull();
  });

  it("someone already signed in goes home", async () => {
    mockApi({ "/api/v1/auth/options": OPTIONS, "/api/v1/auth/me": { ...ME, mfa_required: false }, "/api/v1/engagements": [] });
    renderWithProviders(<AppRoutes />, { route: "/signup" });
    expect(await screen.findByText(/^Good (morning|afternoon|evening)/)).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Create your account" })).toBeNull();
  });

  it("before the firm is set up, the setup page opens", async () => {
    let fresh = false;
    mockApi({
      "/api/v1/auth/options": () => ({ ...OPTIONS, setup_required: fresh }),
      "/api/v1/auth/me": SIGNED_OUT,
      "/api/v1/auth/signup": () => {
        fresh = true;
        return reply(409, { error: { code: "setup_required", message: "FS-Platform is not set up yet" } });
      },
    });
    await openSignup();
    fillForm();
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByRole("form", { name: "Set up the firm" })).toBeInTheDocument();
  });

  it("where an authenticator is also required, the new account sets it up next", async () => {
    let created = false;
    mockApi({
      "/api/v1/auth/options": { ...OPTIONS, mfa_required: true },
      "/api/v1/auth/me": () => (created ? { ...RAVI, mfa_required: true, permissions: {} } : SIGNED_OUT()),
      "/api/v1/auth/signup": () => {
        created = true;
        return reply(201, { ...CREATED, status: "mfa_enrollment_required" });
      },
    });
    await openSignup();
    fillForm();
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByText("Set up your authenticator")).toBeInTheDocument();
  });
});
