import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { generateSync } from "otplib";

export const HERE = dirname(fileURLToPath(import.meta.url));
export const FSUI = resolve(HERE, "..");
export const ROOT = resolve(FSUI, "..");
export const STATE_FILE = join(HERE, ".e2e-state.json");
export const API_PORT = Number(process.env.FSUI_E2E_API_PORT ?? 8601);
export const UI_PORT = 5602;

export interface DemoUser {
  email: string;
  role: string;
  password: string;
  totp_secret: string;
}

export interface E2EState {
  tmp: string;
  pids: number[];
  users: DemoUser[];
  clientId: string;
  engagementId: string;
  workbook: string;
  samplePdf: string;
}

export function saveState(s: E2EState): void {
  writeFileSync(STATE_FILE, JSON.stringify(s, null, 2));
}

export function loadState(): E2EState {
  if (!existsSync(STATE_FILE)) throw new Error("e2e state missing: the global setup did not run");
  return JSON.parse(readFileSync(STATE_FILE, "utf8")) as E2EState;
}

export function user(state: E2EState, role: string): DemoUser {
  const u = state.users.find((x) => x.role === role);
  if (!u) throw new Error(`no demo user with role ${role}`);
  return u;
}

/** The current TOTP code; waits for a fresh 30-second step when the current one is about to expire. */
export async function totp(secret: string): Promise<string> {
  const remaining = 30 - (Math.floor(Date.now() / 1000) % 30);
  if (remaining < 4) await new Promise((r) => setTimeout(r, (remaining + 1) * 1000));
  return generateSync({ secret });
}

export function python(): string {
  return process.env.FSUI_PYTHON ?? join(ROOT, ".venv", "bin", "python");
}
