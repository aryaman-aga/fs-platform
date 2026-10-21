import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end smoke suite. The global setup provisions a throw-away backend
 * (SQLite + local object store, seeded demo tenant), starts the API on
 * 127.0.0.1:8601, a job worker, and `vite preview` on 127.0.0.1:5602 proxying
 * /api to it. The global teardown stops everything and deletes the data.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  globalSetup: "./e2e/global-setup.ts",
  globalTeardown: "./e2e/global-teardown.ts",
  use: {
    baseURL: "http://127.0.0.1:5602",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    actionTimeout: 20_000,
    navigationTimeout: 30_000,
  },
  projects: [
    // the reviewer flow first; the screen sweep then runs on the data it left behind
    { name: "smoke", testMatch: /smoke\.spec\.ts$/, use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    { name: "screens", testMatch: /screens\.spec\.ts$/, dependencies: ["smoke"], use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
  ],
});
