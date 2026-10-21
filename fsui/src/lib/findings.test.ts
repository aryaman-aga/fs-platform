import { describe, expect, it } from "vitest";

import type { FindingLike } from "./findings";
import { countBySeverity, distinctSheets, filterFindings, sortFindings, statusChangeError } from "./findings";

const f = (fid: string, severity: string, rule_id: string, sheet: string | null, status = "Open", caption = ""): FindingLike => ({
  id: fid,
  fid,
  rule_id,
  severity,
  sheet,
  cells: ["C12"],
  caption,
  explanation: `${rule_id} on ${sheet}`,
  status,
});

const items = [
  f("F-0003", "Low", "TXT-07", "Note 6-11"),
  f("F-0001", "High", "CAST-01", "BS", "Open", "Total assets"),
  f("F-0002", "High", "PLUG-01", "Note 6-11", "Not an issue"),
  f("F-0010", "Medium", "CAST-02", "Note 3-5"),
  f("F-0004", "Info", "ENG-02", null, "Resolved"),
];

describe("findings filters", () => {
  it("filters by severity, rule prefix, sheet, status and text", () => {
    expect(filterFindings(items, { severity: ["High"] }).map((x) => x.fid)).toEqual(["F-0001", "F-0002"]);
    expect(filterFindings(items, { rule: "cast" }).map((x) => x.fid)).toEqual(["F-0001", "F-0010"]);
    expect(filterFindings(items, { sheet: "Note 6-11" }).map((x) => x.fid)).toEqual(["F-0003", "F-0002"]);
    expect(filterFindings(items, { status: ["Open"] })).toHaveLength(3);
    expect(filterFindings(items, { text: "total assets" }).map((x) => x.fid)).toEqual(["F-0001"]);
    expect(filterFindings(items, { severity: ["High"], status: ["Open"], rule: "CAST" }).map((x) => x.fid)).toEqual(["F-0001"]);
  });

  it("sorts by severity then numeric id", () => {
    expect(sortFindings(items).map((x) => x.fid)).toEqual(["F-0001", "F-0002", "F-0010", "F-0003", "F-0004"]);
  });

  it("counts and lists sheets", () => {
    expect(countBySeverity(items)).toEqual({ High: 2, Medium: 1, Low: 1, Info: 1 });
    expect(distinctSheets(items)).toEqual(["BS", "Note 3-5", "Note 6-11"]);
  });

  it("requires a reason for 'Not an issue'", () => {
    expect(statusChangeError("Not an issue", "")).toMatch(/reason/i);
    expect(statusChangeError("Not an issue", "presentation only")).toBeNull();
    expect(statusChangeError("Resolved", "")).toBeNull();
  });
});
