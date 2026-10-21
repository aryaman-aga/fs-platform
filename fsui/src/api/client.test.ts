import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, CSRF_HEADER, api, buildHeaders, buildUrl, filenameFrom, readCookie, setCsrfToken, setSessionEndHandler } from "./client";

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
}

describe("API client", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    document.cookie = "fs_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
    setCsrfToken(null);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    setSessionEndHandler(null);
  });

  it("sends the CSRF cookie value in X-CSRF-Token on unsafe methods only", async () => {
    document.cookie = "fs_csrf=tok-123; path=/";
    fetchMock.mockImplementation(async () => jsonResponse(200, { ok: true }));
    await api("/engagements/e1/transition", { method: "POST", body: { to: "REVIEW" } });
    await api("/engagements");
    const post = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const get = fetchMock.mock.calls[1]?.[1] as RequestInit;
    expect(new Headers(post.headers).get(CSRF_HEADER)).toBe("tok-123");
    expect(new Headers(get.headers).get(CSRF_HEADER)).toBeNull();
    expect(post.credentials).toBe("same-origin");
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/engagements/e1/transition");
  });

  it("falls back to the token returned by login when the cookie is unavailable", async () => {
    setCsrfToken("from-login");
    fetchMock.mockResolvedValue(jsonResponse(200, {}));
    await api("/auth/mfa/verify", { method: "POST", body: { code: "123456" } });
    expect(new Headers((fetchMock.mock.calls[0]?.[1] as RequestInit).headers).get(CSRF_HEADER)).toBe("from-login");
  });

  it("adds reason, If-Match, idempotency and correlation headers", () => {
    const h = buildHeaders("POST", { body: {}, reason: "leaver per HR ticket", ifMatch: 'W/"3"', idempotencyKey: "k-1" }, "cid-12345678");
    expect(h.get("X-Action-Reason")).toBe("leaver per HR ticket");
    expect(h.get("If-Match")).toBe('W/"3"');
    expect(h.get("Idempotency-Key")).toBe("k-1");
    expect(h.get("X-Correlation-ID")).toBe("cid-12345678");
    expect(h.get("Content-Type")).toBe("application/json");
  });

  it("turns the error body into an ApiError with the correlation id", async () => {
    fetchMock.mockResolvedValue(jsonResponse(409, { error: { code: "gate_failed", message: "FINAL_LOCKED preconditions are not met", correlation_id: "abc-123", details: { gates: [] } } }));
    const err = await api("/x", { method: "POST" }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe("gate_failed");
    expect((err as ApiError).correlationId).toBe("abc-123");
    expect((err as ApiError).details).toEqual({ gates: [] });
  });

  it("calls the session-end handler on an idle timeout, but not for quiet requests", async () => {
    const handler = vi.fn();
    setSessionEndHandler(handler);
    fetchMock.mockImplementation(async () => jsonResponse(401, { error: { code: "session_idle_timeout", message: "timed out", correlation_id: "c1" } }));
    await api("/engagements").catch(() => undefined);
    expect(handler).toHaveBeenCalledTimes(1);
    await api("/auth/me", { quiet401: true }).catch(() => undefined);
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("does not treat MFA failures as the end of the session", async () => {
    const handler = vi.fn();
    setSessionEndHandler(handler);
    fetchMock.mockResolvedValue(jsonResponse(401, { error: { code: "mfa_invalid", message: "bad code" } }));
    await api("/auth/step-up", { method: "POST", body: {} }).catch(() => undefined);
    expect(handler).not.toHaveBeenCalled();
  });

  it("builds query strings and parses download names", () => {
    expect(buildUrl("/runs/r1/findings", { severity: "High", rule: undefined, sheet: "" })).toBe("/api/v1/runs/r1/findings?severity=High");
    expect(filenameFrom('attachment; filename="review-notes.csv"', "x")).toBe("review-notes.csv");
    expect(filenameFrom("attachment; filename*=UTF-8''Statements%20v2.pdf", "x")).toBe("Statements v2.pdf");
    expect(readCookie("b", "a=1; b=two%20words; c=3")).toBe("two words");
  });
});
