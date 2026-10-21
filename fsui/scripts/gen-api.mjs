// Regenerate the API contract: fsweb's OpenAPI document (src/api/openapi.json),
// then the TypeScript types the client is written against (src/api/schema.d.ts).
// FSUI_PYTHON overrides the interpreter (default: the repository's .venv).
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const fsui = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const root = resolve(fsui, "..");
const python = process.env.FSUI_PYTHON ?? join(root, ".venv", "bin", "python");
if (!existsSync(python)) {
  console.error(`no Python at ${python}: create the repository venv (make setup) or set FSUI_PYTHON`);
  process.exit(1);
}

const spec = join(fsui, "src", "api", "openapi.json");
const types = join(fsui, "src", "api", "schema.d.ts");
execFileSync(python, ["-m", "fsweb.manage", "openapi", "--out", spec], {
  cwd: root,
  stdio: "inherit",
  env: { ...process.env, FSWEB_ENV: process.env.FSWEB_ENV ?? "dev" },
});
execFileSync(process.execPath, [join(fsui, "node_modules", "openapi-typescript", "bin", "cli.js"), spec, "-o", types], { cwd: fsui, stdio: "inherit" });
console.log(`API contract regenerated: ${spec} → ${types}`);
