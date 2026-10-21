import { describe, expect, it } from "vitest";

import { formatAmount, formatCount, formatDate, formatDateTime, groupDigits, relativeTime, roundDecimalString } from "./format";

describe("number grouping", () => {
  it("groups by lakh / crore", () => {
    expect(groupDigits("1234567", "indian")).toBe("12,34,567");
    expect(groupDigits("123456789", "indian")).toBe("12,34,56,789");
    expect(groupDigits("100000", "indian")).toBe("1,00,000");
    expect(groupDigits("999", "indian")).toBe("999");
  });
  it("groups by thousands / millions", () => {
    expect(groupDigits("1234567", "western")).toBe("1,234,567");
    expect(groupDigits("1000", "western")).toBe("1,000");
  });
});

describe("formatAmount", () => {
  it("formats like the statements: brackets for negatives, dash for zero", () => {
    expect(formatAmount(1234567.891)).toBe("12,34,567.89");
    expect(formatAmount(1234567.891, { grouping: "western" })).toBe("1,234,567.89");
    expect(formatAmount(-1253.04)).toBe("(1,253.04)");
    expect(formatAmount("-1253.04", { brackets: false })).toBe("-1,253.04");
    expect(formatAmount(0)).toBe("-");
    expect(formatAmount("0.004")).toBe("-");
    expect(formatAmount(0, { dashForZero: false })).toBe("0.00");
    expect(formatAmount(null)).toBe("");
  });
  it("rounds half away from zero on the decimal string (no float drift)", () => {
    expect(formatAmount("2.675")).toBe("2.68");
    expect(formatAmount("-2.675")).toBe("(2.68)");
    expect(formatAmount("99.995")).toBe("100.00");
    expect(formatAmount(1.005)).toBe("1.01");
    expect(formatAmount("12345.5", { decimals: 0 })).toBe("12,346");
  });
  it("accepts printed strings with grouping and brackets", () => {
    expect(formatAmount("(1,253.04)")).toBe("(1,253.04)");
    expect(formatAmount("12,34,567.8")).toBe("12,34,567.80");
  });
  it("rounds decimal strings", () => {
    expect(roundDecimalString("0.995", 2)).toBe("1.00");
    expect(roundDecimalString("-0.125", 2)).toBe("-0.13");
    expect(roundDecimalString("5", 2)).toBe("5.00");
  });
  it("formats counts", () => {
    expect(formatCount(10250004)).toBe("1,02,50,004");
    expect(formatCount(10250004, "western")).toBe("10,250,004");
  });
});

describe("dates", () => {
  it("uses DD-MMM-YYYY", () => {
    expect(formatDate("2026-03-31")).toBe("31-Mar-2026");
    expect(formatDate("2025-12-01")).toBe("01-Dec-2025");
    expect(formatDate(null)).toBe("");
    expect(formatDate("not a date")).toBe("");
  });
  it("adds the local time for timestamps", () => {
    const d = new Date(2026, 9, 21, 9, 5);
    expect(formatDateTime(d)).toBe("21-Oct-2026 09:05");
  });
  it("describes relative times", () => {
    const now = new Date("2026-10-21T10:00:00Z");
    expect(relativeTime("2026-10-21T13:12:00Z", now)).toBe("in 3 h 12 min");
    expect(relativeTime("2026-10-21T09:42:00Z", now)).toBe("18 min ago");
  });
});
