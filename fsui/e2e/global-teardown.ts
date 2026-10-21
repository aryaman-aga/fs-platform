import { existsSync, rmSync } from "node:fs";

import { STATE_FILE, loadState } from "./env";

export default async function globalTeardown(): Promise<void> {
  if (!existsSync(STATE_FILE)) return;
  const state = loadState();
  for (const pid of state.pids) {
    try {
      process.kill(-pid, "SIGTERM"); // the whole process group (vite preview spawns children)
    } catch {
      try {
        process.kill(pid, "SIGTERM");
      } catch {
        // already gone
      }
    }
  }
  await new Promise((r) => setTimeout(r, 500));
  if (process.env.FSUI_E2E_KEEP !== "1") rmSync(state.tmp, { recursive: true, force: true });
  rmSync(STATE_FILE, { force: true });
}
