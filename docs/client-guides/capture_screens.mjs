#!/usr/bin/env node
/**
 * Capture the screenshots for the FS-Platform user guide by driving the real desktop edition in a browser.
 *
 *     node docs/client-guides/capture_screens.mjs --data-dir /tmp/fs-guide-data
 *
 * The script starts a throw-away FS-Platform on an EMPTY data folder (never a real one), plays a small firm's day with
 * two invented people — Asha Menon, the administrator, and her colleague Meera Iyer — and saves the screens as PNG
 * files (1366 × 800 at twice the pixel density) together with `manifest.json`, which records where the buttons and
 * fields are on each picture, so that user_guide.py can frame them. It stops FS-Platform at the end.
 * An FS-Platform already running on that folder (`python -m fsoffline --data-dir … serve`) is used as it is, as long as
 * nobody has set it up yet; it is left running.
 *
 * Only synthetic data is used: the workbook comes from fscore's synthetic generator (the fictional Corvus group) and
 * nothing leaves this computer. Passwords are made up for each run, typed but never shown: every filled password
 * field is covered in the pictures, and so are the one-time links. They are written to `capture-accounts.json` in the
 * throw-away data folder, should you want to sign in and look around with --keep-running.
 *
 * Playwright comes from fsui (`npm ci` there, then `npx playwright install chromium`).
 */

import { spawn, spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const require = createRequire(path.join(REPO, "fsui", "package.json"));
const { chromium } = require("@playwright/test");

const VIEW = { width: 1366, height: 800 };
const MASK_COLOUR = "#c5ced8"; // covers filled password fields and one-time links

// The invented firm, people and client. The client's names match the synthetic workbook (fscore.synthetic).
const FIRM = "Corvus Advisors";
const ASHA = { name: "Asha Menon", email: "asha@corvus.example", password: madeUpPassword() };
const MEERA = { name: "Meera Iyer", email: "meera@corvus.example", password: madeUpPassword() };
const CLIENT = { name: "Corvus Data Services", group: "Corvus", office: "Bengaluru" };
const ENTITY = "Corvus Data Services Private Limited";
const ENGAGEMENT = { name: "FY 2025-26 statutory accounts", periodEnd: "2026-03-31", due: "2026-11-30" };
const WORKBOOK = "Corvus Data Services FY 2025-26.xlsx";
const CORRECTED = "Corvus Data Services FY 2025-26 corrected.xlsx";
const BACKUP_FOLDER_EXAMPLE = "D:\\FS-Platform backups";

const LONG = 300_000; // checks, fix plans and PDF renders run in the background

function madeUpPassword() {
  return `Guide ${randomBytes(6).toString("hex")} tea cups at noon`;
}

// ------------------------------------------------------------------------------------------------ arguments

const USAGE = `usage: node docs/client-guides/capture_screens.mjs --data-dir <empty folder> [options]

  --data-dir <dir>   an empty folder for a throw-away FS-Platform (required; never your real data folder)
  --out <dir>        where the PNG files and manifest.json go (default: build/client-guides/screens)
  --python <path>    the Python with fsoffline installed (default: .venv/bin/python)
  --keep-running     leave FS-Platform running at the end (to look around; the made-up passwords are in
                     <data-dir>/capture-accounts.json)
  --headed           show the browser while it works`;

const { values: args } = parseArgs({
  options: {
    "data-dir": { type: "string" },
    out: { type: "string", default: path.join(REPO, "build", "client-guides", "screens") },
    python: { type: "string", default: path.join(REPO, ".venv", "bin", "python") },
    "keep-running": { type: "boolean", default: false },
    headed: { type: "boolean", default: false },
    help: { type: "boolean", short: "h", default: false },
  },
});
if (args.help) {
  console.log(USAGE);
  process.exit(0);
}
if (!args["data-dir"]) {
  console.error(USAGE);
  process.exit(2);
}
const DATA_DIR = path.resolve(args["data-dir"]);
const OUT = path.resolve(args.out);
const PYTHON = args.python.includes(path.sep) ? path.resolve(args.python) : args.python; // a path, or a command on PATH

function log(text) {
  console.log(`[${new Date().toTimeString().slice(0, 8)}] ${text}`);
}

// ------------------------------------------------------------------------------------------------ the app

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function readRuntime() {
  try {
    const rt = JSON.parse(fs.readFileSync(path.join(DATA_DIR, "runtime.json"), "utf8"));
    process.kill(rt.pid, 0); // throws when the process is gone
    return rt;
  } catch {
    return null;
  }
}

async function healthy(base) {
  try {
    return (await fetch(new URL("api/v1/health", base))).ok;
  } catch {
    return false;
  }
}

/** The running app's address; starts FS-Platform on the empty data folder first unless it already runs there. */
async function startApp() {
  const running = readRuntime();
  if (running && (await healthy(running.url))) {
    log(`using the FS-Platform already running on ${DATA_DIR}`);
    return { base: running.url, child: null };
  }
  if (fs.existsSync(DATA_DIR) && fs.readdirSync(DATA_DIR).length > 0) {
    throw new Error(`${DATA_DIR} is not empty. Give an empty (or new) folder: the guide starts from a fresh FS-Platform.`);
  }
  fs.mkdirSync(DATA_DIR, { recursive: true });
  log(`starting a throw-away FS-Platform on ${DATA_DIR}`);
  const child = spawn(PYTHON, ["-m", "fsoffline", "--data-dir", DATA_DIR, "serve", "--no-browser"], { cwd: REPO, stdio: "ignore" });
  let exited = null;
  child.on("exit", (code) => {
    exited = code;
  });
  const deadline = Date.now() + 180_000;
  while (Date.now() < deadline) {
    if (exited !== null) throw new Error(`FS-Platform stopped while starting (exit ${exited}); see ${path.join(DATA_DIR, "logs")}`);
    const rt = readRuntime();
    if (rt && (await healthy(rt.url))) return { base: rt.url, child };
    await sleep(500);
  }
  throw new Error("FS-Platform did not start within 3 minutes");
}

async function stopApp(child) {
  log("stopping FS-Platform");
  const exited = child.exitCode !== null ? Promise.resolve() : new Promise((resolve) => child.once("exit", resolve));
  // not spawnSync: Node must keep running to collect the server process, or `stop` would wait for it in vain
  const stop = spawn(PYTHON, ["-m", "fsoffline", "--data-dir", DATA_DIR, "stop"], { cwd: REPO, stdio: "inherit" });
  await new Promise((resolve) => stop.once("exit", resolve));
  await Promise.race([exited, sleep(30_000)]);
}

/** The synthetic workbook with the usual review errors, and the same workbook as it looks once they are corrected. */
function makeWorkbooks(dir) {
  fs.mkdirSync(dir, { recursive: true });
  const first = path.join(dir, WORKBOOK);
  const corrected = path.join(dir, CORRECTED);
  const code = [
    "import sys",
    "from pathlib import Path",
    "from fscore.synthetic.generator import make_workbook",
    "from fsweb.manage import DEMO_INJECTIONS",
    "make_workbook(Path(sys.argv[1]), seed=7, inject=list(DEMO_INJECTIONS))",
    "make_workbook(Path(sys.argv[2]), seed=7)",
  ].join("\n");
  const run = spawnSync(PYTHON, ["-c", code, first, corrected], { cwd: REPO, stdio: "inherit" });
  if (run.status !== 0) throw new Error("the synthetic workbooks could not be made");
  return { first, corrected };
}

function runBackup() {
  const run = spawnSync(PYTHON, ["-X", "utf8", "-m", "fsoffline", "--data-dir", DATA_DIR, "backup"], { cwd: REPO, encoding: "utf8" });
  if (run.status !== 0) throw new Error(`backup failed: ${run.stderr || run.stdout}`);
}

// ------------------------------------------------------------------------------------------------ pictures

const manifest = { app_version: "", captured: "", viewport: VIEW, scale: 2, people: {}, shots: {} };

function saveManifest() {
  fs.writeFileSync(path.join(OUT, "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`);
}

async function boxOf(locator) {
  const el = locator.first();
  if (!(await el.count()) || !(await el.isVisible())) return null;
  const b = await el.boundingBox();
  return b ? [b.x, b.y, b.width, b.height].map((v) => Math.round(v * 2) / 2) : null;
}

/** Visible password fields with something typed in them: covered in every picture. */
async function filledPasswords(page) {
  const fields = page.locator('input[type="password"]');
  const out = [];
  for (let i = 0; i < (await fields.count()); i += 1) {
    const f = fields.nth(i);
    if ((await f.isVisible()) && (await f.inputValue())) out.push(f);
  }
  return out;
}

/**
 * Save the visible window as `<name>.png` and note where `marks` (and the usual regions) are, in CSS pixels.
 * Toasts are waited away unless `keepToasts`; the focus ring is removed; the pointer rests in a corner. `facts` are
 * values shown on the picture that the guide quotes (so that its words keep matching the picture).
 */
async function shot(page, name, { marks = {}, mask = [], keepToasts = false, facts = null } = {}) {
  if (!keepToasts) {
    await page.locator(".toasts .toast").first().waitFor({ state: "detached", timeout: 20_000 }).catch(() => {});
  }
  await page.evaluate(() => {
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
  });
  await page.mouse.move(3, VIEW.height - 3);
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(400);
  const regions = {
    sidebar: page.locator("nav.sidebar"),
    topbar: page.locator("header.topbar"),
    main: page.locator("main#main"),
    card: page.locator(".auth-card"),
    dialog: page.locator('[role="dialog"]'),
    toast: page.locator(".toasts .toast"),
  };
  const boxes = {};
  for (const [key, loc] of Object.entries(regions)) {
    const b = await boxOf(loc);
    if (b) boxes[key] = b;
  }
  for (const [key, loc] of Object.entries(marks)) {
    await loc.first().waitFor({ state: "visible", timeout: 15_000 }).catch(() => {});
    const b = await boxOf(loc);
    if (!b) throw new Error(`${name}: "${key}" is not on the screen`);
    boxes[key] = b;
  }
  const covered = [...(await filledPasswords(page)), ...mask];
  const covers = [];
  for (const c of covered) {
    const b = await boxOf(c);
    if (b) covers.push(b);
  }
  await page.screenshot({
    path: path.join(OUT, `${name}.png`),
    mask: covered,
    maskColor: MASK_COLOUR,
    animations: "disabled",
    caret: "hide",
  });
  manifest.shots[name] = { file: `${name}.png`, width: VIEW.width, height: VIEW.height, marks: boxes, covers, ...(facts ? { facts } : {}) };
  saveManifest();
  log(`  saved ${name}.png`);
}

// ------------------------------------------------------------------------------------------------ helpers

async function openTab(page, key) {
  await page.locator(`#tab-${key}`).click();
  await page.locator(`#tab-${key}[aria-selected="true"]`).waitFor();
}

/** Wait until the person's last "Confirm it's you" has lapsed, so the next administrative step asks for the password. */
async function waitForConfirmationToLapse(page) {
  for (;;) {
    const me = await page.evaluate(async () => (await fetch("/api/v1/auth/me", { credentials: "same-origin" })).json());
    const until = me?.session?.stepup_valid_until ? new Date(me.session.stepup_valid_until).getTime() : 0;
    const left = until - Date.now();
    if (left < 10_000) return;
    log(`  waiting ${Math.ceil(left / 1000)} s until FS-Platform asks for the password again`);
    await sleep(Math.min(left - 5_000, 30_000));
  }
}

async function expectNoRunFailure(page) {
  const failed = page.locator('[data-testid="run-watcher"] .notice.error');
  if (await failed.count()) throw new Error(`a run failed: ${await failed.first().innerText()}`);
}

// ------------------------------------------------------------------------------------------------ the story

/** The web UI FS-Platform serves is fsui/dist: it must exist, and it should not be older than its sources. */
function checkUiBuild() {
  const dist = path.join(REPO, "fsui", "dist", "index.html");
  if (!fs.existsSync(dist)) throw new Error("the web UI is not built: run `npm run build` in fsui first");
  const built = fs.statSync(dist).mtimeMs;
  const newer = [];
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(p);
      else if (!/\.test\.tsx?$/.test(entry.name) && fs.statSync(p).mtimeMs > built) newer.push(path.relative(REPO, p));
    }
  };
  walk(path.join(REPO, "fsui", "src"));
  if (newer.length) log(`warning: fsui/dist is older than ${newer[0]}${newer.length > 1 ? ` and ${newer.length - 1} more` : ""}: run \`npm run build\` in fsui`);
}

async function main() {
  checkUiBuild();
  fs.mkdirSync(OUT, { recursive: true });
  for (const f of fs.readdirSync(OUT)) if (f.endsWith(".png") || f === "manifest.json") fs.rmSync(path.join(OUT, f));
  const workbooks = makeWorkbooks(path.join(OUT, "..", "workbooks"));
  const { base, child } = await startApp();
  if (child && !args["keep-running"]) {
    // Ctrl+C (or a stop from outside) still stops the FS-Platform this script started
    for (const signal of ["SIGINT", "SIGTERM"]) {
      process.once(signal, () => {
        log(`${signal}: stopping`);
        stopApp(child).finally(() => process.exit(130));
      });
    }
  }
  let browser = null;
  let done = false;
  try {
    const options = await (await fetch(new URL("api/v1/auth/options", base))).json();
    if (!options.setup_required) throw new Error("this FS-Platform is already set up; start from an empty data folder");
    if (options.edition !== "desktop" || options.mfa_required || !options.signup) {
      throw new Error(`expected the desktop edition with password sign-in and "Create an account": ${JSON.stringify(options)}`);
    }
    const token = fs.readFileSync(path.join(DATA_DIR, "secrets", "setup.token"), "ascii").trim();
    manifest.app_version = readRuntime()?.version ?? "";
    manifest.captured = new Date().toISOString();
    manifest.people = { firm: FIRM, administrator: ASHA.name, colleague: MEERA.name, client: CLIENT.name, engagement: ENGAGEMENT.name };
    fs.writeFileSync(
      path.join(DATA_DIR, "capture-accounts.json"),
      `${JSON.stringify({ note: "made-up accounts of a throw-away FS-Platform", accounts: [ASHA, MEERA] }, null, 2)}\n`,
      { mode: 0o600 },
    );

    browser = await chromium.launch({ headless: !args.headed });
    const contextOptions = {
      baseURL: base,
      viewport: VIEW,
      deviceScaleFactor: 2,
      locale: "en-IN",
      timezoneId: "Asia/Kolkata",
      colorScheme: "light",
      acceptDownloads: true,
    };
    const newPerson = async () => {
      const page = await (await browser.newContext(contextOptions)).newPage();
      page.setDefaultTimeout(30_000);
      return page;
    };
    const asha = await newPerson();
    const meera = await newPerson();
    try {
      await story({ asha, meera, token, workbooks });
    } catch (err) {
      for (const [who, page] of [["asha", asha], ["meera", meera]]) {
        await page.screenshot({ path: path.join(OUT, `failed-${who}.png`) }).catch(() => {});
      }
      throw err;
    }
    done = true;
  } finally {
    if (browser) await browser.close();
    if (child && !args["keep-running"]) await stopApp(child);
    else if (child) log(`FS-Platform keeps running on ${base} (stop it with: ${PYTHON} -m fsoffline --data-dir ${DATA_DIR} stop)`);
    if (done) log(`done: ${Object.keys(manifest.shots).length} pictures in ${OUT}`);
  }
}

async function story({ asha, meera, token, workbooks }) {
  // ---- first start: the administrator sets up the firm (normally done right after installing) ---------------
  log("setup and sign-in");
  await asha.goto(`setup#token=${token}`);
  await asha.getByLabel("Firm name").fill(FIRM);
  await asha.getByLabel("Your name").fill(ASHA.name);
  await asha.getByLabel("Your e-mail").fill(ASHA.email);
  await asha.getByLabel("Password", { exact: true }).fill(ASHA.password);
  await asha.getByLabel("Repeat password").fill(ASHA.password);
  const createFirm = asha.getByRole("button", { name: "Create the firm" });
  await shot(asha, "setup", { marks: { create: createFirm, works: asha.getByLabel(/I also work on engagements/) } });
  await createFirm.click();
  await asha.getByText("Your firm is ready").waitFor();
  await shot(asha, "firm-ready", { marks: { signin: asha.getByRole("link", { name: "Sign in" }) } });
  await asha.getByRole("link", { name: "Sign in" }).click();
  await asha.waitForURL(/\/login$/);

  // ---- signing in -------------------------------------------------------------------------------------------
  await asha.getByRole("button", { name: "Sign in" }).waitFor();
  await shot(asha, "sign-in", {
    marks: {
      email: asha.getByLabel("E-mail"),
      password: asha.getByLabel("Password"),
      signin: asha.getByRole("button", { name: "Sign in" }),
      forgot: asha.getByRole("link", { name: "Forgot password?" }),
      create: asha.getByRole("link", { name: "Create an account" }),
    },
  });
  await asha.getByLabel("Password").fill(ASHA.password);
  await asha.getByRole("button", { name: "Sign in" }).click();
  await asha.getByRole("heading", { level: 1, name: /, Asha$/ }).waitFor();
  await shot(asha, "dashboard-first", {
    marks: {
      clients: asha.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Clients & entities" }),
      newEngagement: asha.getByRole("link", { name: "New engagement" }),
      signout: asha.getByTestId("sign-out"),
    },
  });

  // ---- a colleague creates her own account: no access yet ---------------------------------------------------
  log("Meera creates her account");
  await meera.goto("login");
  await meera.getByRole("link", { name: "Create an account" }).click();
  await meera.waitForURL(/\/signup$/);
  await meera.getByLabel("Full name").fill(MEERA.name);
  await meera.getByLabel("E-mail").fill(MEERA.email);
  await meera.getByLabel("Password", { exact: true }).fill(MEERA.password);
  await meera.getByLabel("Confirm password").fill(MEERA.password);
  await shot(meera, "sign-up", { marks: { create: meera.getByRole("button", { name: "Create account" }) } });
  await meera.getByRole("button", { name: "Create account" }).click();
  await meera.getByTestId("no-role").waitFor();
  await shot(meera, "no-role", {
    marks: { notice: meera.getByTestId("no-role"), check: meera.getByRole("button", { name: "Check again" }) },
  });

  // ---- the first client, entity and engagement --------------------------------------------------------------
  log("client, entity and engagement");
  await asha.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Clients & entities" }).click();
  await asha.getByRole("heading", { level: 1, name: "Clients & entities" }).waitFor();
  await shot(asha, "clients", {
    marks: {
      nav: asha.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Clients & entities" }),
      newClient: asha.getByRole("button", { name: "New client" }),
    },
  });
  await asha.getByRole("button", { name: "New client" }).click();
  let dialog = asha.getByRole("dialog", { name: "New client" });
  await dialog.getByLabel("Name", { exact: true }).fill(CLIENT.name);
  await dialog.getByLabel("Group").fill(CLIENT.group);
  await dialog.getByLabel("Office").fill(CLIENT.office);
  await shot(asha, "new-client", {
    marks: { name: dialog.getByLabel("Name", { exact: true }), create: dialog.getByRole("button", { name: "Create" }) },
  });
  await dialog.getByRole("button", { name: "Create" }).click();
  await asha.getByRole("heading", { level: 1, name: CLIENT.name }).waitFor();
  await shot(asha, "client-page", {
    marks: {
      addEntity: asha.getByRole("button", { name: "Add entity" }),
      newEngagement: asha.getByRole("button", { name: "New engagement" }),
    },
  });
  await asha.getByRole("button", { name: "Add entity" }).click();
  dialog = asha.getByRole("dialog", { name: "Add entity" });
  await dialog.getByLabel("Name", { exact: true }).fill(ENTITY);
  await shot(asha, "add-entity", {
    marks: { name: dialog.getByLabel("Name", { exact: true }), add: dialog.getByRole("button", { name: "Add", exact: true }) },
  });
  await dialog.getByRole("button", { name: "Add", exact: true }).click();
  await dialog.waitFor({ state: "detached" });
  await asha.locator(".tree").getByText(ENTITY).waitFor();
  await asha.getByRole("button", { name: "New engagement" }).click();
  dialog = asha.getByRole("dialog", { name: "New engagement" });
  await dialog.getByLabel("Name", { exact: true }).fill(ENGAGEMENT.name);
  await dialog.getByLabel("Period end").fill(ENGAGEMENT.periodEnd);
  await dialog.getByLabel("Due date").fill(ENGAGEMENT.due);
  await shot(asha, "new-engagement", {
    marks: {
      entity: dialog.getByLabel("Entity"),
      name: dialog.getByLabel("Name", { exact: true }),
      create: dialog.getByRole("button", { name: "Create" }),
    },
  });
  await dialog.getByRole("button", { name: "Create" }).click();
  await asha.waitForURL(/\/engagements\/[^/]+/);
  const engagementUrl = new URL(asha.url()).pathname.split("/").slice(0, 3).join("/").replace(/^\//, "");
  await asha.getByRole("heading", { level: 1, name: ENGAGEMENT.name }).waitFor();
  await shot(asha, "workspace-new", {
    marks: { rail: asha.locator(".rail"), tabs: asha.getByRole("tablist"), files: asha.locator("#tab-files") },
  });

  // ---- uploading the workbook -------------------------------------------------------------------------------
  log("upload");
  await openTab(asha, "files");
  await shot(asha, "files-empty", {
    marks: { kind: asha.getByLabel("File type"), drop: asha.locator(".dropzone"), choose: asha.getByTestId("file-input") },
  });
  await asha.getByTestId("file-input").setInputFiles(workbooks.first);
  await asha.getByRole("link", { name: "run checks on it now" }).waitFor({ timeout: 120_000 });
  await shot(asha, "files-uploaded", {
    keepToasts: true,
    marks: { runNow: asha.getByRole("link", { name: "run checks on it now" }), files: asha.getByRole("table", { name: "Files" }) },
  });

  // ---- checks -----------------------------------------------------------------------------------------------
  log("checks");
  await asha.getByRole("link", { name: "run checks on it now" }).click();
  await asha.locator('#tab-checks[aria-selected="true"]').waitFor();
  await asha.getByTestId("run-checks").click();
  await asha.getByTestId("run-watcher").waitFor();
  await shot(asha, "checks-running", { keepToasts: true, marks: { watcher: asha.getByTestId("run-watcher") } });
  const findings = asha.getByRole("table", { name: "Findings" });
  await findings.locator("tbody tr").first().waitFor({ timeout: LONG });
  await expectNoRunFailure(asha);
  await shot(asha, "checks", {
    marks: {
      run: asha.getByTestId("run-checks"),
      stats: asha.locator(".grid-4").first(),
      filters: asha.getByRole("group", { name: "Filters" }),
      findings,
      details: asha.locator(".side-panel"),
    },
  });
  // a High finding that a total does not add up: the classic casting error
  const rows = findings.locator("tbody tr");
  let row = rows.filter({ hasText: /CAST/ }).filter({ hasText: "High" }).first();
  if (!(await row.count())) row = rows.first();
  await row.click();
  const panel = asha.locator(".side-panel");
  await panel.getByRole("button", { name: "Save" }).waitFor();
  // the details panel sticks below the top bar and scrolls on its own: show its top, then its bottom
  await panel.evaluate((el) => {
    window.scrollTo(0, window.scrollY + el.getBoundingClientRect().top - 64);
    el.scrollTop = 0;
  });
  // what the details say (Caption, Printed, Expected …), quoted by the guide
  const facts = await panel.evaluate((el) => {
    const out = {};
    for (const dt of el.querySelectorAll("dl.kv dt")) {
      const dd = dt.nextElementSibling;
      if (dd) out[dt.textContent.trim().toLowerCase()] = dd.textContent.trim();
    }
    return out;
  });
  await shot(asha, "finding", {
    facts,
    marks: {
      row,
      panel,
      title: panel.locator(".card-header").first(),
      explanation: panel.locator("p").first(),
      fix: panel.getByText(/^Suggested fix:/),
    },
  });
  await panel.evaluate((el) => {
    // the panel also holds the key figures: scroll it to the Status field of the finding, not to the very end
    const status = [...el.querySelectorAll("label")].find((l) => l.textContent?.trim() === "Status");
    if (status) el.scrollTop += status.getBoundingClientRect().top - el.getBoundingClientRect().top - 24;
  });
  await shot(asha, "finding-actions", {
    marks: {
      panel,
      status: panel.getByLabel("Status"),
      save: panel.getByRole("button", { name: "Save" }),
      assign: panel.getByRole("button", { name: "Assign to me" }),
      note: panel.getByLabel("Raise a review note on this finding"),
    },
  });
  await asha.evaluate(() => window.scrollTo(0, 0));

  // ---- the administrator gives Meera a role -------------------------------------------------------------------
  log("Meera gets a role");
  await asha.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Administration" }).click();
  await asha.getByTestId("no-role-hint").waitFor();
  const meeraRow = asha.getByRole("table", { name: "Users" }).locator("tbody tr").filter({ hasText: MEERA.email });
  await shot(asha, "users-no-role", {
    marks: {
      admin: asha.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Administration" }),
      hint: asha.getByTestId("no-role-hint"),
      row: meeraRow,
      badge: meeraRow.getByText("No role yet"),
      give: meeraRow.getByRole("link", { name: "Give a role" }),
    },
  });
  await meeraRow.getByRole("link", { name: "Give a role" }).click();
  await asha.getByRole("heading", { name: "Assign a role" }).waitFor();
  await asha.getByLabel("Role").first().selectOption({ label: "Manager" });
  await asha.getByLabel("Client").first().selectOption({ label: CLIENT.name });
  const assign = asha.getByRole("button", { name: "Assign", exact: true });
  await shot(asha, "assign", {
    marks: {
      card: asha.locator("section.card").filter({ has: asha.getByRole("heading", { name: "Assign a role" }) }),
      user: asha.getByLabel("User"),
      role: asha.getByLabel("Role").first(),
      client: asha.getByLabel("Client").first(),
      assign,
    },
  });
  await waitForConfirmationToLapse(asha);
  await assign.click();
  dialog = asha.getByRole("dialog", { name: "Assign role" });
  await dialog.getByLabel(/^Reason/).fill("Meera prepares the FY 2025-26 accounts");
  await dialog.getByLabel("Your password").fill(ASHA.password);
  await shot(asha, "confirm", {
    marks: {
      reason: dialog.getByLabel(/^Reason/),
      password: dialog.getByLabel("Your password"),
      confirm: dialog.getByRole("button", { name: "Confirm" }),
    },
  });
  await dialog.getByRole("button", { name: "Confirm" }).click();
  await dialog.waitFor({ state: "detached" });
  const assignments = asha.getByRole("table", { name: "Role assignments" });
  await assignments.getByText(MEERA.email).waitFor();
  await shot(asha, "assigned", {
    keepToasts: true,
    marks: { list: assignments, meera: assignments.locator("tbody tr").filter({ hasText: MEERA.email }) },
  });

  // ---- Meera now sees the client --------------------------------------------------------------------------------
  log("Meera proposes the fix plan");
  await meera.getByRole("button", { name: "Check again" }).click();
  const meeraEngagement = meera.getByRole("table", { name: "My engagements" }).getByRole("link", { name: ENGAGEMENT.name });
  await shot(meera, "meera-dashboard", { marks: { engagement: meeraEngagement } });
  await meeraEngagement.click();
  await meera.getByRole("heading", { level: 1, name: ENGAGEMENT.name }).waitFor();
  await openTab(meera, "fix");
  await meera.getByTestId("propose-plan").waitFor();
  await shot(meera, "fix-propose", { marks: { propose: meera.getByTestId("propose-plan"), tab: meera.locator("#tab-fix") } });
  await meera.getByTestId("propose-plan").click();
  const ownPlan = meera.getByText("You proposed this plan: a different person must approve it");
  await ownPlan.waitFor({ timeout: LONG });
  await shot(meera, "fix-proposed", {
    marks: { notice: ownPlan, approve: meera.getByTestId("approve-plan"), plan: meera.locator("section.card").first() },
  });

  // ---- Asha approves, applies and verifies ----------------------------------------------------------------------
  log("Asha approves and applies");
  await asha.goto(`${engagementUrl}/fix`);
  await asha.getByTestId("approve-plan").waitFor();
  const picks = asha.getByRole("checkbox", { name: /^Approve / });
  for (let i = 0; i < (await picks.count()); i += 1) await picks.nth(i).check();
  // the approve button at the top and, below it, the first correction that needs a tick
  await asha.getByTestId("approve-plan").evaluate((el) => window.scrollTo(0, window.scrollY + el.getBoundingClientRect().top - 72));
  await shot(asha, "fix-approve", {
    marks: {
      approve: asha.getByTestId("approve-plan"),
      reject: asha.getByRole("button", { name: "Reject" }),
      ...((await picks.count()) ? { pick: picks.first() } : {}),
    },
  });
  await asha.getByTestId("approve-plan").click();
  const balancing = asha.getByRole("button", { name: "Approve balancing (checker)" });
  const apply = asha.getByTestId("apply-plan");
  await balancing.or(apply).first().waitFor({ timeout: 60_000 });
  if (await balancing.isVisible()) {
    await balancing.click();
  }
  await apply.waitFor();
  await shot(asha, "fix-approved", { keepToasts: true, marks: { apply } });
  await apply.click();
  const verified = asha.getByText("Verification passed");
  await verified.or(asha.getByText("Verification failed")).first().waitFor({ timeout: LONG });
  await expectNoRunFailure(asha);
  if (!(await verified.isVisible())) throw new Error("the fix plan did not verify");
  await shot(asha, "fix-verified", {
    keepToasts: true,
    marks: { result: asha.locator("section.card").filter({ hasText: "Verification passed" }) },
  });
  // the corrected version is checked too: what is left is for Excel
  await openTab(asha, "checks");
  await asha.getByTestId("run-checks").click();
  await asha.getByText("Checks finished").waitFor({ timeout: LONG });
  await expectNoRunFailure(asha);
  await shot(asha, "checks-after-fix", {
    marks: { run: asha.getByTestId("run-checks"), stats: asha.locator(".grid-4").first() },
  });

  // ---- PDFs ---------------------------------------------------------------------------------------------------
  log("draft PDF");
  await openTab(asha, "pdf");
  await asha.getByTestId("render-draft").waitFor();
  await shot(asha, "pdf-tab", {
    marks: {
      draft: asha.getByTestId("render-draft"),
      final: asha.getByRole("button", { name: "Render FINAL" }),
      engine: asha.getByLabel("Engine"),
      gates: asha.getByText("FINAL PDF gates"),
    },
  });
  await render(asha, asha.getByTestId("render-draft"), "draft");
  await shot(asha, "pdf-draft", {
    marks: {
      engine: asha.getByLabel("Engine"),
      draft: asha.getByTestId("render-draft"),
      final: asha.getByRole("button", { name: "Render FINAL" }),
      gates: asha.getByText("FINAL PDF gates"),
      recon: asha.locator("section.card").filter({ has: asha.getByRole("heading", { name: /^Reconciliation/ }) }),
      matches: asha.getByText("0 misses — PDF matches the workbook"),
      viewer: asha.getByTestId("pdf-viewer"),
      banner: asha.locator(".watermark-banner"),
      download: asha.getByRole("button", { name: /Download \(watermarked\)/ }),
    },
  });
  const viewerCard = asha.locator("section.card").filter({ has: asha.getByTestId("pdf-viewer") });
  await viewerCard.evaluate((el) => window.scrollTo(0, window.scrollY + el.getBoundingClientRect().top - 64));
  await shot(asha, "pdf-draft-viewer", {
    marks: {
      card: viewerCard,
      viewer: asha.getByTestId("pdf-viewer"),
      banner: asha.locator(".watermark-banner"),
      download: asha.getByRole("button", { name: /Download \(watermarked\)/ }),
    },
  });
  // the middle of the first page, where the DRAFT marking runs across it
  const firstPage = asha.getByTestId("pdf-viewer").locator("canvas").first();
  await firstPage.evaluate((el) => {
    const r = el.getBoundingClientRect();
    window.scrollTo(0, window.scrollY + r.top + r.height / 2 - window.innerHeight / 2);
  });
  await shot(asha, "pdf-draft-watermark", { marks: { page: firstPage } });
  await asha.evaluate(() => window.scrollTo(0, 0));
  const downloads = path.join(OUT, "..", "downloads");
  fs.mkdirSync(downloads, { recursive: true });
  const [download] = await Promise.all([
    asha.waitForEvent("download"),
    asha.getByRole("button", { name: /Download \(watermarked\)/ }).click(),
  ]);
  await download.saveAs(path.join(downloads, "draft-watermarked.pdf"));

  // ---- a tour of the screen, with work in progress (before anything fails or gets locked) ------------------
  log("tour");
  await asha.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Dashboard", exact: true }).click();
  await asha.getByRole("heading", { level: 1, name: /, Asha$/ }).waitFor();
  const nav = asha.getByRole("navigation", { name: "Main" });
  await shot(asha, "tour", {
    marks: {
      nav,
      dashboard: nav.getByRole("link", { name: "Dashboard", exact: true }),
      clients: nav.getByRole("link", { name: "Clients & entities" }),
      administration: nav.getByRole("link", { name: "Administration" }),
      help: nav.getByRole("link", { name: "Help", exact: true }),
      firm: asha.locator("header.topbar .tenant"),
      helpButton: asha.getByRole("button", { name: "Help for this screen" }),
      me: asha.locator("header.topbar").getByRole("link", { name: ASHA.name }),
      signout: asha.getByTestId("sign-out"),
      stats: asha.locator(".grid-4").first(),
      engagements: asha.getByRole("table", { name: "My engagements" }),
      newEngagement: asha.getByRole("link", { name: "New engagement" }),
    },
  });
  await asha.getByRole("button", { name: "Help for this screen" }).click();
  await asha.getByRole("complementary", { name: "Help for this screen" }).waitFor();
  await shot(asha, "help", { marks: { drawer: asha.getByRole("complementary", { name: "Help for this screen" }) } });
  await asha.getByRole("button", { name: "Close help" }).click();

  // the corrected workbook: checked again automatically, no High findings left, so a FINAL PDF may be made
  log("corrected workbook and final PDF");
  await asha.goto(`${engagementUrl}/files`);
  await asha.locator('#tab-files[aria-selected="true"]').waitFor();
  await asha.getByTestId("file-input").setInputFiles(workbooks.corrected);
  await asha.getByText(/Checks started automatically/).waitFor({ timeout: 120_000 });
  await shot(asha, "files-corrected", { keepToasts: true, marks: { watcher: asha.getByTestId("run-watcher") } });
  await asha.getByTestId("run-watcher").locator(".chip", { hasText: /^(succeeded|failed)$/ }).waitFor({ timeout: LONG });
  await expectNoRunFailure(asha);
  await openTab(asha, "checks");
  await asha.locator(".grid-4").first().waitFor({ timeout: LONG });
  await shot(asha, "checks-corrected", { marks: { stats: asha.locator(".grid-4").first() } });
  await openTab(asha, "pdf");
  const final = asha.getByRole("button", { name: "Render FINAL" });
  const finalReady = await asha
    .waitForFunction(() => {
      const b = [...document.querySelectorAll("button")].find((x) => x.textContent?.trim() === "Render FINAL");
      return !!b && !b.disabled;
    }, null, { timeout: 60_000 })
    .then(() => true)
    .catch(() => false);
  await shot(asha, "pdf-final-ready", { marks: { final, gates: asha.getByText("FINAL PDF gates") } });
  let finalDone = false;
  if (finalReady) {
    try {
      await render(asha, final, "final");
      finalDone = true;
    } catch (err) {
      // e.g. "a final PDF needs a current check run …": noted, pictured for the record, and the story goes on
      manifest.notes = [...(manifest.notes ?? []), `Render FINAL failed: ${err.message}`];
      log(`  warning: ${err.message}`);
      await shot(asha, "pdf-final-failed", { marks: { error: asha.getByTestId("render-failed") } });
    }
  }
  if (finalDone) {
    const recon = asha.locator("section.card").filter({ has: asha.getByRole("heading", { name: /^Reconciliation/ }) });
    await shot(asha, "pdf-final", {
      marks: {
        render: asha.locator("section.card").filter({ has: asha.getByRole("heading", { name: "Render", exact: true }) }),
        recon,
        mode: recon.locator(".chip", { hasText: /^final$/ }),
        matches: recon.getByText("0 misses — PDF matches the workbook"),
        download: asha.getByRole("button", { name: /Download \(watermarked\)/ }),
      },
    });
    await viewerCard.evaluate((el) => window.scrollTo(0, window.scrollY + el.getBoundingClientRect().top - 64));
    await shot(asha, "pdf-final-viewer", { marks: { viewer: asha.getByTestId("pdf-viewer") } });
    const finalPage = asha.getByTestId("pdf-viewer").locator("canvas").first();
    await finalPage.evaluate((el) => {
      const r = el.getBoundingClientRect();
      window.scrollTo(0, window.scrollY + r.top + r.height / 2 - window.innerHeight / 2);
    });
    await shot(asha, "pdf-final-watermark", { marks: { page: finalPage } });
    const [finalDownload] = await Promise.all([
      asha.waitForEvent("download"),
      asha.getByRole("button", { name: /Download \(watermarked\)/ }).click(),
    ]);
    await finalDownload.saveAs(path.join(downloads, "final-watermarked.pdf"));
    await asha.evaluate(() => window.scrollTo(0, 0));
  } else if (!finalReady) {
    manifest.notes = [...(manifest.notes ?? []), "Render FINAL stayed unavailable after the corrected workbook was checked"];
    log("  warning: Render FINAL stayed unavailable; no final PDF pictures");
  }

  // ---- the other tabs -----------------------------------------------------------------------------------------
  log("review notes, checklist and audit timeline");
  await openTab(asha, "notes");
  const raise = asha.locator("section.card").filter({ has: asha.getByRole("heading", { name: "Raise a review note" }) });
  await raise.getByLabel("Note").fill("Please attach the bank confirmation for the fixed deposits before we sign.");
  await raise.getByRole("button", { name: "Raise note" }).click();
  await asha.getByText("Please attach the bank confirmation").first().waitFor();
  await meera.goto(`${engagementUrl}/notes`);
  const thread = meera.locator("section.card").filter({ hasText: "Please attach the bank confirmation" });
  await thread.getByLabel("Respond").fill("Done: the confirmation is in the Files tab.");
  await thread.getByRole("button", { name: "Send" }).click();
  await thread.getByText("Done: the confirmation is in the Files tab.").waitFor();
  await asha.reload();
  await asha.getByText("Done: the confirmation is in the Files tab.").waitFor();
  const ashaThread = asha.locator("section.card").filter({ hasText: "Please attach the bank confirmation" });
  await shot(asha, "review-notes", {
    marks: { thread: ashaThread, clear: ashaThread.getByRole("button", { name: "Clear" }), raise },
  });
  await openTab(asha, "checklist");
  await asha.getByRole("button", { name: "Auto-answer from latest check" }).click();
  await asha.getByText("Answered what the engine could detect").waitFor();
  await shot(asha, "checklist", { marks: { auto: asha.getByRole("button", { name: "Auto-answer from latest check" }) } });
  await openTab(asha, "audit");
  await asha.getByRole("table", { name: "Audit events" }).waitFor();
  await shot(asha, "audit", { marks: { events: asha.getByRole("table", { name: "Audit events" }) } });

  // ---- administrator corner -----------------------------------------------------------------------------------
  log("administration: password reset and backups");
  await asha.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Administration" }).click();
  const users = asha.getByRole("table", { name: "Users" });
  await users.waitFor();
  const meeraUser = users.locator("tbody tr").filter({ hasText: MEERA.email });
  await shot(asha, "users", {
    marks: { users, reset: meeraUser.getByRole("button", { name: "Reset password" }), sections: asha.getByRole("navigation", { name: "Administration sections" }) },
  });
  await meeraUser.getByRole("button", { name: "Reset password" }).click();
  dialog = asha.getByRole("dialog", { name: /^Reset password for/ });
  await dialog.getByLabel(/^Reason/).fill("Meera forgot her password");
  const pw = dialog.getByLabel("Your password");
  if (await pw.count()) await pw.fill(ASHA.password);
  await shot(asha, "reset-confirm", { marks: { confirm: dialog.getByRole("button", { name: "Confirm" }) } });
  await dialog.getByRole("button", { name: "Confirm" }).click();
  const resetLink = asha.getByTestId("reset-link");
  await resetLink.waitFor();
  await shot(asha, "reset-link", {
    keepToasts: true,
    mask: [resetLink.locator("code")],
    marks: { link: resetLink, copy: resetLink.getByRole("button", { name: "Copy link" }) },
  });
  runBackup();
  await asha.getByRole("navigation", { name: "Administration sections" }).getByRole("link", { name: "Backups & restore" }).click();
  await asha.getByTestId("backup-copy-folder").waitFor();
  await asha.getByRole("table", { name: "Backup log" }).waitFor();
  await asha.getByLabel("Backup copy folder").fill(BACKUP_FOLDER_EXAMPLE);
  const backupLog = asha.getByRole("table", { name: "Backup log" });
  await shot(asha, "backups", {
    // the Location column shows the throw-away folder on this computer (user name and all): covered
    mask: [backupLog.locator("tbody td:nth-child(5)")],
    marks: {
      folder: asha.getByLabel("Backup copy folder"),
      save: asha.getByTestId("backup-copy-folder").getByRole("button", { name: "Save" }),
      log: backupLog,
    },
  });
  await asha.getByLabel("Backup copy folder").fill("");

  // ---- troubleshooting: a forgotten password and a locked account -------------------------------------------------
  log("forgotten password and locked account");
  await meera.getByTestId("sign-out").click();
  await meera.waitForURL(/\/login/);
  await meera.getByRole("link", { name: "Forgot password?" }).click();
  await meera.getByText("Ask your administrator to reset your password.").waitFor();
  await shot(meera, "forgot", { marks: { notice: meera.getByText("Ask your administrator to reset your password.") } });
  await meera.getByRole("link", { name: "Back to sign in" }).click();
  // five wrong passwords lock the account for 15 minutes; the sixth attempt says so
  for (let attempt = 1; attempt <= 6; attempt += 1) {
    await meera.getByLabel("E-mail").fill(MEERA.email);
    await meera.getByLabel("Password").fill(`not the password ${attempt}`);
    await Promise.all([
      meera.waitForResponse((r) => r.url().endsWith("/api/v1/auth/login")),
      meera.getByRole("button", { name: "Sign in" }).click(),
    ]);
    await meera.getByRole("alert").first().waitFor();
    if (await meera.getByText(/temporarily locked/).count()) break;
  }
  await meera.getByText(/temporarily locked/).waitFor();
  await meera.getByLabel("Password").fill("");
  await shot(meera, "locked", {
    marks: {
      message: meera.getByRole("alert").first(),
      email: meera.getByLabel("E-mail"),
      signin: meera.getByRole("button", { name: "Sign in" }),
    },
  });
}

/** Click a render button and wait until the PDF of that mode is shown and reconciled (or the render failed). */
async function render(page, button, mode) {
  await button.click();
  await page.getByTestId("run-watcher").waitFor({ timeout: 15_000 }).catch(() => {});
  await page.getByTestId("run-watcher").waitFor({ state: "detached", timeout: LONG });
  const failed = page.getByTestId("render-failed");
  if (await failed.isVisible()) throw new Error(`render failed: ${await failed.innerText()}`);
  const recon = page.locator("section.card").filter({ has: page.getByRole("heading", { name: /^Reconciliation/ }) });
  await recon.locator(".chip", { hasText: new RegExp(`^${mode}$`) }).waitFor({ timeout: 30_000 });
  await recon.getByText("0 misses — PDF matches the workbook").waitFor({ timeout: 30_000 });
  await page.getByTestId("pdf-viewer").locator("canvas").first().waitFor({ timeout: LONG });
  await page.waitForFunction(() => {
    const m = /^(\d+) of (\d+)/.exec(document.querySelector('[data-testid="pdf-pages"]')?.textContent ?? "");
    return !!m && Number(m[1]) >= Math.min(2, Number(m[2]));
  }, null, { timeout: LONG });
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
