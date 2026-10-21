import { describe, expect, it } from "vitest";

import { getYamlValue, setYamlValue } from "./yaml";

const doc = `client: Synthetic Demo Private Limited
year_end: '2026-03-31'
period_type: annual
units: lakhs
policy:
  casting: totals_only
  plug_threshold: '0.99'
  balancing_threshold: '0.20'
  balancing:
    balance_sheet:
      caption: ^\\s*other expenses\\s*$
ai:
  provider: none
signing_window_days: 365
`;

describe("configuration YAML form editing", () => {
  it("reads top-level and nested scalars", () => {
    expect(getYamlValue(doc, "units")).toBe("lakhs");
    expect(getYamlValue(doc, "year_end")).toBe("2026-03-31");
    expect(getYamlValue(doc, "policy.balancing_threshold")).toBe("0.20");
    expect(getYamlValue(doc, "ai.provider")).toBe("none");
    expect(getYamlValue(doc, "ai.region")).toBeNull();
    expect(getYamlValue(doc, "missing")).toBeNull();
  });

  it("replaces values and keeps their quoting and everything else", () => {
    const out = setYamlValue(setYamlValue(doc, "policy.balancing_threshold", "0.10"), "units", "millions");
    expect(getYamlValue(out, "policy.balancing_threshold")).toBe("0.10");
    expect(out).toContain("  balancing_threshold: '0.10'");
    expect(out).toContain("units: millions");
    expect(out).toContain("      caption: ^\\s*other expenses\\s*$");
    expect(out.split("\n")).toHaveLength(doc.split("\n").length);
  });

  it("adds a missing child under an existing parent and a missing block at the end", () => {
    const withRegion = setYamlValue(doc, "ai.region", "ap-south-1");
    expect(getYamlValue(withRegion, "ai.region")).toBe("ap-south-1");
    expect(withRegion).toMatch(/ai:\n {2}region: ap-south-1\n {2}provider: none/);
    const withRender = setYamlValue(doc, "render.engine", "libreoffice");
    expect(getYamlValue(withRender, "render.engine")).toBe("libreoffice");
  });

  it("quotes values that YAML would misread", () => {
    expect(setYamlValue("a: x\n", "a", "b: c")).toBe("a: 'b: c'\n");
  });
});
