/// <reference types="vitest/config" />
import { readFileSync } from "node:fs";
import { fileURLToPath, URL } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The SPA talks to the FastAPI backend on the same origin: in development and in
// `vite preview` the API is proxied, in production Caddy (or FSWEB_SERVE_UI in the
// offline edition) serves both. FSUI_API_TARGET points the proxy at the backend.
const apiTarget = process.env.FSUI_API_TARGET ?? "http://127.0.0.1:8000";

const proxy = {
  "/api": { target: apiTarget, changeOrigin: false, ws: false },
  "/metrics": { target: apiTarget, changeOrigin: false },
};

// `vite preview` sends the production Content-Security-Policy, read from the Caddy
// edge config, so the e2e suite exercises the built bundle under the same rules.
function edgeCsp(): string | undefined {
  try {
    const caddy = readFileSync(fileURLToPath(new URL("../ops/caddy/Caddyfile", import.meta.url)), "utf8");
    return /Content-Security-Policy "([^"]+)"/.exec(caddy)?.[1];
  } catch {
    return undefined; // the UI image builds from fsui/ alone
  }
}
const csp = edgeCsp();

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: { port: 5601, strictPort: true, proxy },
  preview: { port: 5602, strictPort: true, proxy, headers: csp ? { "Content-Security-Policy": csp } : {} },
  build: {
    outDir: "dist",
    sourcemap: false,
    target: "es2022",
    chunkSizeWarningLimit: 1600,
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
