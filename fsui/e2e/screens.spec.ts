import type { Page } from "@playwright/test";
import { expect, test } from "@playwright/test";

import type { E2EState } from "./env";
import { loadState, user } from "./env";
import { signIn, signOut } from "./session";

/**
 * Every screen renders for the roles that reach it, on the data the smoke flow
 * left behind: no uncaught exception, no error boundary, and no API call
 * failing with 4xx/5xx (a permitted screen must not call what its role cannot).
 */
test.describe.configure({ mode: "serial" });

const TABS = ["files", "checks", "fix", "narrative", "rpt", "pdf", "notes", "client", "checklist", "ai", "audit", "settings"];

let state: E2EState;
let page: Page;
let problems: string[] = [];

test.beforeAll(async ({ browser }) => {
  state = loadState();
  page = await browser.newPage();
  page.on("pageerror", (e) => problems.push(`uncaught: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() === "error" && /Content Security Policy|Refused to/.test(m.text())) problems.push(`csp: ${m.text()}`);
  });
  page.on("response", (r) => {
    const url = new URL(r.url());
    if (!url.pathname.startsWith("/api/") || r.status() < 400) return;
    if (r.status() === 401 && url.pathname === "/api/v1/auth/me") return; // the signed-out probe on /login
    problems.push(`${r.status()} ${r.request().method()} ${url.pathname}`);
  });
});

test.afterAll(async () => {
  await page.close();
});

async function settle(where: string): Promise<void> {
  await expect(page.locator("main h1").first()).toBeVisible();
  await page.waitForLoadState("networkidle");
  await expect(page.getByTestId("screen-error"), where).toHaveCount(0);
  expect(problems, where).toEqual([]);
}

async function visit(path: string): Promise<void> {
  problems = [];
  await page.goto(path);
  await settle(path);
}

test("firm admin: clients, audit, rules, every admin section, account and help", async () => {
  await signIn(page, user(state, "firm_admin"));
  for (const path of ["/", "/clients", `/clients/${state.clientId}`, "/audit", "/rules", "/profile", "/help", "/release-notes", "/no-such-page"]) {
    await visit(path);
  }
  await visit("/admin");
  const sections = page.getByRole("navigation", { name: "Administration sections" }).getByRole("link");
  const hrefs = await sections.evaluateAll((els) => els.map((e) => e.getAttribute("href") ?? ""));
  expect(hrefs.length).toBeGreaterThan(5);
  for (const href of hrefs) await visit(href);
  await signOut(page);
});

test("system admin: operations sections", async () => {
  await signIn(page, user(state, "system_admin"));
  await visit("/admin");
  const hrefs = await page
    .getByRole("navigation", { name: "Administration sections" })
    .getByRole("link")
    .evaluateAll((els) => els.map((e) => e.getAttribute("href") ?? ""));
  expect(hrefs).toContain("/admin/health");
  for (const href of hrefs) await visit(href);
  await signOut(page);
});

test("partner: every engagement tab", async () => {
  await signIn(page, user(state, "partner"));
  for (const tab of TABS) await visit(`/engagements/${state.engagementId}/${tab}`);
});

test("partner: the PDF viewer (pdf.js under the production CSP) shows a draft with the watermark banner", async () => {
  // the render engine is not under test here: serve one succeeded draft render and its PDF
  const pdfVersion = "e2e-sample-pdf";
  const now = new Date().toISOString();
  const run = {
    id: "e2e-render-run", engagement_id: state.engagementId, kind: "render", status: "succeeded", input_version_ids: [], input_hashes: [],
    engine_version: "e2e", rule_set_version: null, rule_set_hash: null, config_hash: null, params_json: { mode: "draft", engine: "native" },
    result_json: { mode: "draft", engine: "native", engine_version: "e2e", pages: 2, misses: 0, pdf_version_id: pdfVersion, verification: { misses: [] } },
    output_version_id: pdfVersion, error_code: null, error_message: null, correlation_id: "e2e", created_by: "e2e", created_at: now, started_at: now, finished_at: now,
  };
  await page.route(
    (url) => url.pathname === `/api/v1/engagements/${state.engagementId}/runs` && url.searchParams.get("kind") === "render",
    (route) => route.fulfill({ json: [run] }),
  );
  await page.route(`**/api/v1/versions/${pdfVersion}/download`, (route) =>
    route.fulfill({ path: state.samplePdf, headers: { "Content-Type": "application/pdf", "Content-Disposition": 'attachment; filename="draft.pdf"' } }),
  );
  await visit(`/engagements/${state.engagementId}/pdf`);
  await expect(page.getByText("0 misses — PDF matches the workbook")).toBeVisible();
  const viewer = page.getByTestId("pdf-viewer");
  await expect(viewer).toContainText("viewed by partner@demo.fs.test");
  await expect(viewer.locator("canvas")).toHaveCount(2);
  await expect(page.getByTestId("pdf-pages")).toHaveText("2 of 2 page(s) shown");
  expect(problems).toEqual([]);
  await page.unrouteAll();
  await signOut(page);
});

for (const role of ["client_viewer", "client_uploader"]) {
  test(`${role}: the portal only`, async () => {
    problems = [];
    await signIn(page, user(state, role));
    await expect(page).toHaveURL(/\/portal$/);
    await settle("/portal");
    const nav = page.getByRole("navigation", { name: "Main" });
    await expect(nav).not.toContainText("Clients");
    await expect(nav).not.toContainText("Admin");
    await visit(`/portal/engagements/${state.engagementId}`);
    await signOut(page);
  });
}
