import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

import { AuthProvider } from "@/auth/AuthContext";
import { PrefsProvider } from "@/components/Prefs";
import { StepUpProvider } from "@/components/StepUp";
import { ToastProvider } from "@/components/Toast";

export type Routes = Record<string, unknown | ((init?: RequestInit) => unknown)>;

/** Mock fetch by path prefix (longest match wins); unmatched requests return 404. */
export function mockApi(routes: Routes) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, init });
    const path = url.replace(/^https?:\/\/[^/]+/, "");
    const key = Object.keys(routes)
      .filter((k) => path === k || path.startsWith(k + "?") || path.startsWith(k))
      .sort((a, b) => b.length - a.length)[0];
    if (!key) return new Response(JSON.stringify({ error: { code: "not_found", message: "not found" } }), { status: 404, headers: { "Content-Type": "application/json" } });
    const r = routes[key];
    const body = typeof r === "function" ? (r as (init?: RequestInit) => unknown)(init) : r;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fn);
  return { fn, calls };
}

export const ME = {
  user: { id: "u-staff", email: "staff@demo.fs.test", name: "Sunil Staff", status: "active", is_firm_user: true, mfa_type: "totp", mfa_enrolled_at: "2026-01-01T00:00:00Z", last_login_at: null, created_at: "2026-01-01T00:00:00Z" },
  tenant_id: "t1",
  tenant_name: "Demo Firm (synthetic)",
  session: { id: "s1", auth_method: "password", created_at: "2026-10-21T00:00:00Z", last_seen_at: "2026-10-21T00:00:00Z", expires_at: "2026-10-21T12:00:00Z", idle_minutes: 60, current: true },
  assignments: [{ id: "a1", user_id: "u-staff", client_id: "c1", client_name: "Synthetic Demo", role_code: "staff", role_name: "Staff / Article", created_at: "2026-01-01T00:00:00Z" }],
  permissions: { "*": { "engagement.view": "allow", "narrative.edit": "maker_checker", "run.check": "allow" }, c1: { "engagement.view": "allow", "narrative.edit": "maker_checker" } },
  ai_policy_version: 1,
  ai_policy_accepted: false,
};

export function renderWithProviders(ui: ReactElement, { route = "/" }: { route?: string } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>
        <PrefsProvider>
          <ToastProvider>
            <AuthProvider>
              <StepUpProvider>{ui}</StepUpProvider>
            </AuthProvider>
          </ToastProvider>
        </PrefsProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
