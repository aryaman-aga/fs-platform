import type { Page } from "@playwright/test";
import { expect } from "@playwright/test";

import type { DemoUser } from "./env";
import { totp } from "./env";

/** Password, then the TOTP code; ends on the landing screen (dashboard, or the portal for client users). */
export async function signIn(page: Page, u: DemoUser): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("E-mail").fill(u.email);
  await page.getByLabel("Password").fill(u.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/mfa$/);
  await page.getByLabel("Authenticator code").fill(await totp(u.totp_secret));
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByRole("heading", { name: /^(Good (morning|afternoon|evening)|Client portal)/ })).toBeVisible();
}

export async function signOut(page: Page): Promise<void> {
  await page.getByTestId("sign-out").click();
  await expect(page).toHaveURL(/\/login/);
}
