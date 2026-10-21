import { describe, expect, it } from "vitest";

import { isCellAddress, isFormulaLike, validateNarrativeText } from "./formula";

describe("C6 — narrative is typed text", () => {
  it.each([
    "=SUM(C4:C6)",
    "  =B12",
    "+C4",
    "@SUM(A1)",
    "-SUM(A1:A3)",
    "The loss is IF(C5>0,1,2)",
    "see ROUND(C12, 2) lakhs",
    "=HYPERLINK(\"http://evil\")",
  ])("rejects %s", (text) => {
    expect(isFormulaLike(text)).toBe(true);
    expect(validateNarrativeText(text).ok).toBe(false);
  });

  it.each([
    "The Company has incurred a loss of ₹ 73.22 lakhs during the year ended March 31, 2026.",
    "-12.34 is shown in brackets as (12.34).",
    "Net worth is positive at ₹ 57.36 lakhs; the holding company will provide support.",
    "Sum insured and rounding are discussed in Note 30.",
  ])("accepts %s", (text) => {
    expect(isFormulaLike(text)).toBe(false);
    expect(validateNarrativeText(text).ok).toBe(true);
  });

  it("requires some text", () => {
    expect(validateNarrativeText("   ").ok).toBe(false);
  });

  it("validates cell addresses", () => {
    expect(isCellAddress("B12")).toBe(true);
    expect(isCellAddress("XFD1048576")).toBe(true);
    expect(isCellAddress("b12")).toBe(false);
    expect(isCellAddress("A0")).toBe(false);
  });
});
