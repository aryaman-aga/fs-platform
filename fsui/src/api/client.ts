/**
 * Thin fetch wrapper for the FS-Platform API (`/api/v1`).
 *
 * - Session: HttpOnly `fs_session` cookie (same origin, SameSite=Strict).
 * - CSRF: double submit — the JS-readable `fs_csrf` cookie (or the token returned
 *   by login / MFA) is echoed in `X-CSRF-Token` on every unsafe method.
 * - Errors: `{error: {code, message, correlation_id, details}}` becomes an
 *   ApiError carrying the correlation id that the UI shows (never stack traces).
 * - Every request carries an `X-Correlation-ID` so a support ticket can quote it.
 */

export const API_BASE = "/api/v1";
export const CSRF_COOKIE = "fs_csrf";
export const CSRF_HEADER = "X-CSRF-Token";
export const REASON_HEADER = "X-Action-Reason";

const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);
const SESSION_CODES = new Set(["unauthenticated", "session_invalid", "session_expired", "session_idle_timeout", "account_disabled"]);

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly correlationId: string | null;
  readonly details: Record<string, unknown> | undefined;

  constructor(status: number, code: string, message: string, correlationId: string | null, details?: Record<string, unknown>) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.correlationId = correlationId;
    this.details = details;
  }

  /** The session ended (idle timeout, signed out elsewhere, disabled account). */
  get isSessionEnd(): boolean {
    return this.status === 401 && SESSION_CODES.has(this.code);
  }
}

let memoryCsrf: string | null = null;
let onSessionEnd: ((err: ApiError) => void) | null = null;

export function setCsrfToken(token: string | null | undefined): void {
  memoryCsrf = token ?? null;
}

export function setSessionEndHandler(fn: ((err: ApiError) => void) | null): void {
  onSessionEnd = fn;
}

export function readCookie(name: string, cookieString: string = typeof document !== "undefined" ? document.cookie : ""): string | null {
  for (const part of cookieString.split(";")) {
    const [k, ...rest] = part.trim().split("=");
    if (k === name) {
      try {
        return decodeURIComponent(rest.join("="));
      } catch {
        return rest.join("=");
      }
    }
  }
  return null;
}

/** The cookie wins: it is rotated by the server on MFA / step-up; memory is a fallback. */
export function csrfToken(): string | null {
  return readCookie(CSRF_COOKIE) ?? memoryCsrf;
}

export function newCorrelationId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `cid-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  form?: FormData;
  query?: Record<string, string | number | boolean | null | undefined>;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  /** Reason for an administrative action (X-Action-Reason, E-SEC-03). */
  reason?: string;
  ifMatch?: string;
  idempotencyKey?: string;
  /** Do not trigger the global "session ended" handler (login / MFA screens). */
  quiet401?: boolean;
}

export function buildUrl(path: string, query?: RequestOptions["query"]): string {
  const base = path.startsWith("http") || path.startsWith(API_BASE) ? path : `${API_BASE}${path.startsWith("/") ? "" : "/"}${path}`;
  if (!query) return base;
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === "") continue;
    qs.set(k, String(v));
  }
  const s = qs.toString();
  return s ? `${base}${base.includes("?") ? "&" : "?"}${s}` : base;
}

export function buildHeaders(method: string, opts: RequestOptions, cid: string): Headers {
  const h = new Headers({ Accept: "application/json", "X-Correlation-ID": cid, ...(opts.headers ?? {}) });
  if (opts.body !== undefined && opts.form === undefined) h.set("Content-Type", "application/json");
  if (UNSAFE.has(method)) {
    const token = csrfToken();
    if (token) h.set(CSRF_HEADER, token);
  }
  if (opts.reason) h.set(REASON_HEADER, opts.reason);
  if (opts.ifMatch) h.set("If-Match", opts.ifMatch);
  if (opts.idempotencyKey) h.set("Idempotency-Key", opts.idempotencyKey);
  return h;
}

async function toError(res: Response, cid: string): Promise<ApiError> {
  let code = `http_${res.status}`;
  let message = res.statusText || "request failed";
  let correlation: string | null = res.headers.get("X-Correlation-ID") ?? cid;
  let details: Record<string, unknown> | undefined;
  try {
    const data = (await res.json()) as { error?: { code?: string; message?: string; correlation_id?: string; details?: Record<string, unknown> } };
    if (data?.error) {
      code = data.error.code ?? code;
      message = data.error.message ?? message;
      correlation = data.error.correlation_id ?? correlation;
      details = data.error.details ?? undefined;
    }
  } catch {
    // non-JSON error body (proxy / gateway): keep the HTTP status text
  }
  return new ApiError(res.status, code, message, correlation, details);
}

async function send(path: string, opts: RequestOptions): Promise<Response> {
  const method = opts.method ?? (opts.body !== undefined || opts.form ? "POST" : "GET");
  const cid = newCorrelationId();
  const init: RequestInit = {
    method,
    credentials: "same-origin",
    headers: buildHeaders(method, opts, cid),
    signal: opts.signal,
    cache: "no-store",
  };
  if (opts.form) init.body = opts.form;
  else if (opts.body !== undefined) init.body = JSON.stringify(opts.body);
  let res: Response;
  try {
    res = await fetch(buildUrl(path, opts.query), init);
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "network_error", "Cannot reach the FS-Platform server. Check your connection.", cid);
  }
  if (!res.ok) {
    const err = await toError(res, cid);
    if (err.isSessionEnd && !opts.quiet401 && onSessionEnd) onSessionEnd(err);
    throw err;
  }
  return res;
}

export async function api<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const res = await send(path, opts);
  if (res.status === 204) return undefined as T;
  const type = res.headers.get("Content-Type") ?? "";
  if (type.includes("application/json")) return (await res.json()) as T;
  return (await res.text()) as unknown as T;
}

export interface DownloadedFile {
  blob: Blob;
  filename: string;
  contentType: string;
}

export function filenameFrom(disposition: string | null, fallback: string): string {
  if (!disposition) return fallback;
  const star = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
  if (star?.[1]) {
    try {
      return decodeURIComponent(star[1]);
    } catch {
      return star[1];
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(disposition);
  return plain?.[1] ?? fallback;
}

export async function apiBlob(path: string, opts: RequestOptions = {}, fallbackName = "download"): Promise<DownloadedFile> {
  const res = await send(path, { ...opts, headers: { Accept: "*/*", ...(opts.headers ?? {}) } });
  const blob = await res.blob();
  return { blob, filename: filenameFrom(res.headers.get("Content-Disposition"), fallbackName), contentType: res.headers.get("Content-Type") ?? blob.type };
}

/** Save a downloaded file through a temporary object URL. */
export function saveFile(file: DownloadedFile): void {
  const url = URL.createObjectURL(file.blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = file.filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

export function describeError(err: unknown): { message: string; code?: string; correlationId?: string | null } {
  if (err instanceof ApiError) return { message: err.message, code: err.code, correlationId: err.correlationId };
  if (err instanceof Error) return { message: err.message };
  return { message: String(err) };
}
