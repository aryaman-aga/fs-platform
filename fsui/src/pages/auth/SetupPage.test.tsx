import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppRoutes } from "@/App";
import { mockApi, renderWithProviders, reply } from "@/test/render";

import { SetupPage } from "./SetupPage";

// no /auth/me route: nobody is signed in on a fresh installation
const FRESH = { setup_required: true, sso: false, edition: "desktop", self_service_reset: false };

function fill(label: string | RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

function fillForm(email = "asha@corvus.test") {
  fill("Firm name", "Corvus & Rao Chartered Accountants");
  fill("Your name", "Asha Rao");
  fill("Your e-mail", email);
  fill(/^Password/, "Ledger-Lantern-Quay-58");
  fill("Repeat password", "Ledger-Lantern-Quay-58");
}

describe("First-run setup", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("creates the firm with the launcher's one-time token and leads to sign-in", async () => {
    const { calls } = mockApi({
      "/api/v1/auth/options": FRESH,
      "/api/v1/setup": { tenant_id: "t1", admin_email: "asha@corvus.test", message: "FS-Platform is set up." },
    });
    renderWithProviders(<SetupPage />, { route: "/setup#token=0ne-T1me-t0ken" });

    expect(await screen.findByText("Desktop edition · runs on this PC only")).toBeInTheDocument();
    const create = screen.getByRole("button", { name: "Create the firm" });
    expect(create).toBeDisabled();
    fillForm();
    fill("Repeat password", "Ledger-Lantern-Quay-5");
    expect(screen.getByText("The passwords differ.")).toBeInTheDocument();
    expect(create).toBeDisabled();
    fill("Repeat password", "Ledger-Lantern-Quay-58");
    fireEvent.click(screen.getByLabelText("I also work on engagements (prepare or review statements)"));
    expect(screen.getByText(/weakens segregation of duties/)).toBeInTheDocument();
    await waitFor(() => expect(create).toBeEnabled());
    fireEvent.click(create);

    expect(await screen.findByText("Your firm is ready")).toBeInTheDocument();
    const post = calls.find((c) => c.url.endsWith("/api/v1/setup"));
    expect(post?.init?.method).toBe("POST");
    expect(JSON.parse(String(post?.init?.body))).toEqual({
      firm_name: "Corvus & Rao Chartered Accountants",
      admin_name: "Asha Rao",
      admin_email: "asha@corvus.test",
      password: "Ledger-Lantern-Quay-58",
      works_on_engagements: true,
      setup_token: "0ne-T1me-t0ken",
    });
    // next: sign in, then the QR-code enrolment of the authenticator
    expect(screen.getByText(/Scan the QR code with an authenticator app/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
  });

  it("shows the server's refusal, e.g. an expired setup link", async () => {
    mockApi({
      "/api/v1/auth/options": FRESH,
      "/api/v1/setup": () => reply(403, { error: { code: "setup_token_missing", message: "this setup link is no longer valid" } }),
    });
    renderWithProviders(<SetupPage />, { route: "/setup" });
    await screen.findByLabelText("Firm name");
    fillForm();
    fireEvent.click(screen.getByRole("button", { name: "Create the firm" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("this setup link is no longer valid");
    expect(screen.queryByText("Your firm is ready")).toBeNull();
  });

  it("a fresh installation opens the setup page wherever the app starts, keeping the token", async () => {
    const { calls } = mockApi({ "/api/v1/auth/options": FRESH, "/api/v1/setup": { tenant_id: "t1", admin_email: "asha@corvus.test" } });
    renderWithProviders(<AppRoutes />, { route: "/login#token=abc123" });
    expect(await screen.findByRole("form", { name: "Set up the firm" })).toBeInTheDocument();
    fillForm();
    fireEvent.click(screen.getByRole("button", { name: "Create the firm" }));
    await screen.findByText("Your firm is ready");
    const post = calls.find((c) => c.url.endsWith("/api/v1/setup"));
    expect(JSON.parse(String(post?.init?.body)).setup_token).toBe("abc123");
  });

  it("an installed firm is not set up again", async () => {
    mockApi({ "/api/v1/auth/options": { ...FRESH, setup_required: false } });
    renderWithProviders(<AppRoutes />, { route: "/setup" });
    expect(await screen.findByText("FS-Platform is already set up")).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Set up the firm" })).toBeNull();
  });
});
