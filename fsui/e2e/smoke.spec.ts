import type { Page } from "@playwright/test";
import { expect, test } from "@playwright/test";

import type { E2EState } from "./env";
import { loadState, user } from "./env";
import { signIn, signOut } from "./session";

/**
 * The happy path a reviewer walks every engagement through: Staff uploads a
 * workbook, checks it and proposes a fix plan; a Manager (a different person,
 * C2/C10) approves and applies it, renders a draft PDF and reads the audit trail.
 * One page is shared so each step starts where the previous one left off.
 */
test.describe.configure({ mode: "serial" });

let state: E2EState;
let page: Page;

test.beforeAll(async ({ browser }) => {
  state = loadState();
  page = await browser.newPage();
});

test.afterAll(async () => {
  await page.close();
});

async function openTab(name: RegExp): Promise<void> {
  await page.getByRole("tab", { name }).click();
  await expect(page.getByRole("tab", { name })).toHaveAttribute("aria-selected", "true");
}

test("staff signs in with password + TOTP and sees the dashboard", async () => {
  await signIn(page, user(state, "staff"));
  await expect(page.getByRole("table", { name: "My engagements" })).toContainText("FY 2025-26 statutory accounts");
  await expect(page.getByRole("navigation", { name: "Main" })).not.toContainText("Admin"); // role-aware navigation
});

test("staff opens the demo engagement and uploads a new workbook version", async () => {
  await page.getByRole("link", { name: "FY 2025-26 statutory accounts" }).first().click();
  await expect(page).toHaveURL(new RegExp(`/engagements/${state.engagementId}`));
  await openTab(/^Files/);
  await page.getByTestId("file-input").setInputFiles(state.workbook);
  await expect(page.getByText(/Uploaded .* as version 2/)).toBeVisible();
  await expect(page.getByRole("table", { name: "Files" })).toContainText("v2 · Synthetic FS FY26 v2.xlsx");
});

test("staff runs the checks and sees findings with severities and cell links", async () => {
  // before verification an upload does not re-run checks by itself (06 §1); the Files tab links here
  await page.getByRole("link", { name: "run checks on it now" }).click();
  await expect(page.getByRole("tab", { name: /^Checks/ })).toHaveAttribute("aria-selected", "true");
  await page.getByTestId("run-checks").click();
  await expect(page.getByText("Checks finished")).toBeVisible({ timeout: 90_000 });
  const grid = page.getByRole("table", { name: "Findings" });
  await expect(grid.locator("tbody tr").first()).toBeVisible();
  await expect(grid).toContainText("High");
  await expect(grid).toContainText(/!\$?[A-Z]+\$?\d+/); // every finding points at sheet!cell
});

test("staff proposes a fix plan and cannot approve it", async () => {
  await openTab(/^Fix plan/);
  await page.getByTestId("propose-plan").click();
  await expect(page.getByText("You proposed this plan: a different person must approve it")).toBeVisible({ timeout: 90_000 });
  const approve = page.getByTestId("approve-plan");
  if (await approve.count()) await expect(approve).toBeDisabled();
  await signOut(page);
});

test("a manager approves and applies the plan; the new version verifies", async () => {
  await signIn(page, user(state, "manager"));
  await page.goto(`/engagements/${state.engagementId}/fix`);
  await page.getByTestId("approve-plan").click();
  await page.getByTestId("apply-plan").click();
  await expect(page.getByText("Verification passed")).toBeVisible({ timeout: 90_000 });
});

test("the manager renders a draft PDF and it is shown with its watermark banner", async () => {
  await openTab(/^PDF/);
  // the built-in renderer needs neither Excel nor LibreOffice, so the result does not depend on the machine
  await page.getByLabel("Engine").selectOption("native");
  await page.getByTestId("render-draft").click();
  const failed = page.getByTestId("render-failed");
  await expect(page.getByText(/0 misses — PDF matches the workbook|miss\(es\)/).or(failed)).toBeVisible({ timeout: 90_000 });
  if (await failed.isVisible()) throw new Error(`render failed: ${(await failed.textContent()) ?? ""}`);
  const viewer = page.getByTestId("pdf-viewer");
  await expect(viewer).toBeVisible({ timeout: 60_000 });
  await expect(viewer).toContainText("manager@demo.fs.test");
  await expect(viewer.locator("canvas").first()).toBeVisible({ timeout: 60_000 });
});

test("the audit timeline shows every step of the flow", async () => {
  await openTab(/^Audit timeline/);
  const events = page.getByRole("table", { name: "Audit events" });
  const expected = ["file.uploaded", "run.succeeded", "fixplan.proposed", "fixplan.approved", "fixplan.applied", "render.completed"];
  for (const action of expected) {
    await expect(events).toContainText(action);
  }
});
