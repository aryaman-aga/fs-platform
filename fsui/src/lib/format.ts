/**
 * Number and date formatting for the English (India) locale (08 §4).
 *
 * Amounts follow the statements' own conventions: negatives in brackets, zero
 * as a dash, and a user toggle between lakh/crore grouping (12,34,567.89) and
 * million grouping (1,234,567.89). Formatting is done on the decimal string so
 * no floating-point rounding is introduced on display.
 */

export type Grouping = "indian" | "western";

export interface AmountOptions {
  grouping?: Grouping;
  decimals?: number;
  /** Show negatives as (1,234.00) — default true. */
  brackets?: boolean;
  /** Show exact zero as "-" — default true. */
  dashForZero?: boolean;
}

/** Group the integer digits of a non-negative integer string. */
export function groupDigits(int: string, grouping: Grouping): string {
  const digits = int.replace(/^0+(?=\d)/, "");
  if (digits.length <= 3) return digits;
  if (grouping === "western") {
    return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }
  const last3 = digits.slice(-3);
  const rest = digits.slice(0, -3);
  return rest.replace(/\B(?=(\d{2})+(?!\d))/g, ",") + "," + last3;
}

/** Round a decimal string half away from zero to `decimals` places (string arithmetic). */
export function roundDecimalString(value: string, decimals: number): string {
  const m = /^(-?)(\d*)(?:\.(\d*))?$/.exec(value.trim());
  if (!m) return value;
  const sign = m[1] ?? "";
  const intPart = m[2] === "" || m[2] === undefined ? "0" : m[2];
  const frac = m[3] ?? "";
  if (frac.length <= decimals) {
    const padded = decimals > 0 ? "." + frac.padEnd(decimals, "0") : "";
    return `${sign}${intPart}${padded}`;
  }
  const keep = intPart + frac.slice(0, decimals);
  const roundUp = Number(frac.charAt(decimals)) >= 5;
  let digits = keep.split("").map(Number);
  if (roundUp) {
    let i = digits.length - 1;
    while (i >= 0) {
      const d = (digits[i] ?? 0) + 1;
      if (d === 10) {
        digits[i] = 0;
        i -= 1;
      } else {
        digits[i] = d;
        break;
      }
    }
    if (i < 0) digits = [1, ...digits];
  }
  const all = digits.join("");
  const intLen = all.length - decimals;
  const outInt = all.slice(0, intLen) || "0";
  const outFrac = decimals > 0 ? "." + all.slice(intLen) : "";
  return `${sign}${outInt}${outFrac}`;
}

function toDecimalString(value: number | string): string | null {
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return null;
    // 15 significant digits, like Excel, then expand exponent notation
    const s = Number(value.toPrecision(15)).toString();
    if (!/e/i.test(s)) return s;
    return Number(value).toFixed(Math.min(20, Math.max(0, -Math.floor(Math.log10(Math.abs(value))) + 15)));
  }
  const t = value.trim().replace(/,/g, "");
  if (t === "" || t === "-") return t === "-" ? "0" : null;
  let neg = false;
  let body = t;
  if (/^\(.*\)$/.test(body)) {
    neg = true;
    body = body.slice(1, -1);
  }
  if (!/^-?\d*\.?\d+$|^-?\d+\.?$/.test(body)) return null;
  if (neg) body = body.startsWith("-") ? body.slice(1) : "-" + body;
  return body;
}

/** Format an amount: 1234567.891 → "12,34,567.89" (indian) or "1,234,567.89" (western); −5 → "(5.00)". */
export function formatAmount(value: number | string | null | undefined, opts: AmountOptions = {}): string {
  const { grouping = "indian", decimals = 2, brackets = true, dashForZero = true } = opts;
  if (value === null || value === undefined) return "";
  const dec = toDecimalString(value);
  if (dec === null) return String(value);
  const rounded = roundDecimalString(dec, decimals);
  const negative = rounded.startsWith("-");
  const abs = negative ? rounded.slice(1) : rounded;
  const [int = "0", frac] = abs.split(".");
  const isZero = !/[1-9]/.test(abs);
  if (dashForZero && isZero) return "-";
  const body = groupDigits(int, grouping) + (frac !== undefined ? "." + frac : "");
  if (!negative || isZero) return body;
  return brackets ? `(${body})` : `-${body}`;
}

/** Plain integer count with grouping (no brackets, zero kept). */
export function formatCount(value: number | null | undefined, grouping: Grouping = "indian"): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "";
  const neg = value < 0;
  const s = groupDigits(String(Math.round(Math.abs(value))), grouping);
  return neg ? `-${s}` : s;
}

export function formatBytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`; // desktop backups
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function toDate(value: string | Date | null | undefined): Date | null {
  if (value === null || value === undefined || value === "") return null;
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
  // a bare ISO date ("2026-03-31") is a calendar date, not an instant: keep it in local time
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (m) return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** DD-MMM-YYYY, e.g. 31-Mar-2026 (08 §4). */
export function formatDate(value: string | Date | null | undefined): string {
  const d = toDate(value);
  if (!d) return "";
  return `${String(d.getDate()).padStart(2, "0")}-${MONTHS[d.getMonth()]}-${d.getFullYear()}`;
}

/** DD-MMM-YYYY HH:MM in local time. */
export function formatDateTime(value: string | Date | null | undefined): string {
  const d = toDate(value);
  if (!d) return "";
  return `${formatDate(d)} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

/** "in 3 h 12 min" / "18 min ago" — used for the CERT-In 6-hour clock and lock expiry. */
export function relativeTime(target: string | Date | null | undefined, now: Date = new Date()): string {
  const d = toDate(target);
  if (!d) return "";
  const diff = Math.round((d.getTime() - now.getTime()) / 60000);
  const abs = Math.abs(diff);
  const h = Math.floor(abs / 60);
  const m = abs % 60;
  const span = h > 0 ? `${h} h ${m} min` : `${m} min`;
  return diff >= 0 ? `in ${span}` : `${span} ago`;
}

export function shortHash(sha: string | null | undefined, n = 12): string {
  return sha ? sha.slice(0, n) : "";
}

export function titleCase(s: string): string {
  return s
    .replace(/[_-]+/g, " ")
    .toLowerCase()
    .replace(/\b\w/g, (c) => c.toUpperCase());
}
