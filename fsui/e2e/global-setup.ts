import { execFileSync, spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { existsSync, mkdtempSync, openSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";

import type { DemoUser } from "./env";
import { API_PORT, FSUI, ROOT, UI_PORT, python, saveState } from "./env";

async function portFree(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const srv = createServer();
    srv.once("error", () => resolve(false));
    srv.once("listening", () => srv.close(() => resolve(true)));
    srv.listen(port, "127.0.0.1");
  });
}

async function waitFor(url: string, timeoutMs = 90_000): Promise<void> {
  const start = Date.now();
  let last = "";
  while (Date.now() - start < timeoutMs) {
    try {
      const res = await fetch(url);
      if (res.ok) return;
      last = `HTTP ${res.status}`;
    } catch (e) {
      last = (e as Error).message;
    }
    await new Promise((r) => setTimeout(r, 400));
  }
  throw new Error(`timed out waiting for ${url} (${last})`);
}

export default async function globalSetup(): Promise<void> {
  for (const port of [API_PORT, UI_PORT]) {
    if (!(await portFree(port))) throw new Error(`port ${port} is in use; stop whatever is listening there and retry`);
  }
  const tmp = mkdtempSync(join(tmpdir(), "fsui-e2e-"));
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    FSWEB_ENV: "dev",
    FSWEB_DATABASE_URL: `sqlite:///${join(tmp, "fsweb.db")}`,
    FSWEB_STORAGE: `local:${join(tmp, "objects")}`,
    FSWEB_WORK_DIR: join(tmp, "work"),
    FSWEB_SECRET_KEY: randomBytes(32).toString("hex"),
    FSWEB_KMS_MASTER_KEY: randomBytes(32).toString("base64"),
    FSWEB_COOKIE_SECURE: "false",
    FSWEB_PUBLIC_BASE_URL: `http://127.0.0.1:${UI_PORT}`,
    FSWEB_LOG_LEVEL: "WARNING",
    PYTHONUNBUFFERED: "1",
  };
  const py = python();
  const run = (args: string[]) => execFileSync(py, args, { cwd: ROOT, env, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], timeout: 180_000 });

  run(["-m", "fsweb.manage", "migrate"]);
  const seedOut = run(["-m", "fsweb.manage", "seed-demo"]);
  const seed = JSON.parse(seedOut.slice(seedOut.indexOf("{"))) as { client_id: string; engagement_id: string; users: DemoUser[] };

  // a synthetic workbook with a typed rounding plug, so the fix plan has real work to do
  const workbook = join(tmp, "Synthetic FS FY26 v2.xlsx");
  run(["-c", `from fscore.synthetic.generator import make_workbook; make_workbook(${JSON.stringify(workbook)}, seed=7, inject=["plug_total_minus"])`]);

  // a small PDF for the viewer check, which must not depend on the render engine
  const samplePdf = join(tmp, "sample-draft.pdf");
  run([
    "-c",
    `from reportlab.pdfgen import canvas; c = canvas.Canvas(${JSON.stringify(samplePdf)}); c.drawString(72, 760, "Synthetic Demo Private Limited - draft statements"); c.showPage(); c.drawString(72, 760, "Page 2"); c.save()`,
  ]);

  if (!existsSync(join(FSUI, "dist", "index.html")) || process.env.FSUI_E2E_BUILD === "1") {
    execFileSync("npx", ["vite", "build"], { cwd: FSUI, stdio: "inherit" });
  }

  const log = (name: string) => openSync(join(tmp, `${name}.log`), "a");
  const api = spawn(py, ["-m", "uvicorn", "fsweb.main:app", "--host", "127.0.0.1", "--port", String(API_PORT)], {
    cwd: ROOT,
    env,
    detached: true,
    stdio: ["ignore", log("api"), log("api")],
  });
  const worker = spawn(py, ["-m", "fsweb.worker", "--poll", "0.3"], { cwd: ROOT, env, detached: true, stdio: ["ignore", log("worker"), log("worker")] });
  const ui = spawn("npx", ["vite", "preview", "--host", "127.0.0.1", "--port", String(UI_PORT), "--strictPort"], {
    cwd: FSUI,
    env: { ...process.env, FSUI_API_TARGET: `http://127.0.0.1:${API_PORT}` },
    detached: true,
    stdio: ["ignore", log("ui"), log("ui")],
  });
  const pids = [api.pid, worker.pid, ui.pid].filter((p): p is number => typeof p === "number");
  saveState({ tmp, pids, users: seed.users, clientId: seed.client_id, engagementId: seed.engagement_id, workbook, samplePdf });
  await waitFor(`http://127.0.0.1:${API_PORT}/api/v1/health`);
  await waitFor(`http://127.0.0.1:${UI_PORT}/`);
  await waitFor(`http://127.0.0.1:${UI_PORT}/api/v1/health`);
}
