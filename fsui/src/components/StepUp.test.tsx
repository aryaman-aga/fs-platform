import { fireEvent, screen, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import { ME, mockApi, renderWithProviders, reply } from "@/test/render";

import { useStepUp } from "./StepUp";

const PASSWORD = "Harbour-Ledger-Spruce-41";
// the confirmation given at sign-in (or the last step-up) has lapsed
const LAPSED = { ...ME.session, stepup_valid_until: null };

/** A privileged action behind "Confirm it's you". */
function DisableMeera() {
  const { me } = useAuth();
  const stepUp = useStepUp();
  const [done, setDone] = useState(false);
  if (!me) return null;
  const disable = () =>
    stepUp.run("Disable meera@corvus.test", (reason) => api("/admin/users/u-meera/disable", { method: "POST", reason })).then(
      () => setDone(true),
      () => undefined,
    );
  return (
    <>
      <button type="button" onClick={() => void disable()}>
        Disable
      </button>
      {done ? <p>Disabled</p> : null}
    </>
  );
}

async function openDialog(): Promise<HTMLElement> {
  renderWithProviders(<DisableMeera />);
  fireEvent.click(await screen.findByRole("button", { name: "Disable" }));
  const dialog = await screen.findByRole("dialog");
  fireEvent.change(within(dialog).getByLabelText("Reason (recorded in the audit trail)"), { target: { value: "left the firm" } });
  return dialog;
}

describe("Confirm it's you (step-up)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("without an authenticator, asks for the password and says plainly when it is wrong", async () => {
    const { calls } = mockApi({
      "/api/v1/auth/me": { ...ME, mfa_required: false, session: LAPSED },
      "/api/v1/auth/step-up": (init?: RequestInit) =>
        JSON.parse(String(init?.body)).password === PASSWORD
          ? { ok: true, message: "step-up verified" }
          : reply(401, { error: { code: "invalid_password", message: "the password is not correct" } }),
      "/api/v1/admin/users/u-meera/disable": { ok: true, message: "user disabled" },
    });
    const dialog = await openDialog();
    expect(within(dialog).getByText("Confirm it's you. Enter your password to continue.")).toBeInTheDocument();
    expect(dialog).not.toHaveTextContent(/authenticator|MFA|code/i);
    const password = within(dialog).getByLabelText("Your password");
    expect(password).toHaveAttribute("type", "password");

    fireEvent.change(password, { target: { value: "Harbour-Ledger-Spruce-14" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirm" }));
    expect(await within(dialog).findByText(/That password isn't right/)).toBeInTheDocument();
    expect(calls.some((c) => c.url.endsWith("/disable"))).toBe(false);

    fireEvent.change(within(dialog).getByLabelText("Your password"), { target: { value: PASSWORD } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirm" }));
    expect(await screen.findByText("Disabled")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
    const stepUps = calls.filter((c) => c.url.endsWith("/auth/step-up")).map((c) => JSON.parse(String(c.init?.body)));
    expect(stepUps).toEqual([{ password: "Harbour-Ledger-Spruce-14" }, { password: PASSWORD }]);
    const disable = calls.find((c) => c.url.endsWith("/admin/users/u-meera/disable"));
    expect(new Headers(disable?.init?.headers).get("X-Action-Reason")).toBe("left the firm");
  });

  it("without an authenticator, a recent confirmation needs only the reason", async () => {
    const { calls } = mockApi({
      "/api/v1/auth/me": { ...ME, mfa_required: false, session: { ...ME.session, stepup_valid_until: "2099-01-01T00:00:00Z" } },
      "/api/v1/admin/users/u-meera/disable": { ok: true, message: "user disabled" },
    });
    const dialog = await openDialog();
    expect(within(dialog).getByText(/You confirmed it's you a moment ago/)).toBeInTheDocument();
    expect(within(dialog).queryByLabelText("Your password")).toBeNull();
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirm" }));
    expect(await screen.findByText("Disabled")).toBeInTheDocument();
    expect(calls.some((c) => c.url.endsWith("/auth/step-up"))).toBe(false);
  });

  it("with an authenticator, asks for the code as before", async () => {
    const { calls } = mockApi({
      "/api/v1/auth/me": { ...ME, session: LAPSED },
      "/api/v1/auth/step-up": { ok: true, message: "step-up verified" },
      "/api/v1/admin/users/u-meera/disable": { ok: true, message: "user disabled" },
    });
    const dialog = await openDialog();
    expect(within(dialog).queryByLabelText("Your password")).toBeNull();
    fireEvent.change(within(dialog).getByLabelText("Authenticator code"), { target: { value: "123 456" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirm" }));
    expect(await screen.findByText("Disabled")).toBeInTheDocument();
    const stepUp = calls.find((c) => c.url.endsWith("/auth/step-up"));
    expect(JSON.parse(String(stepUp?.init?.body))).toEqual({ code: "123456" });
  });
});
