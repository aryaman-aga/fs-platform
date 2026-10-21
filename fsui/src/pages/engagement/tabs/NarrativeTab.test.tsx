import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EngagementOut } from "@/api/types";
import { ME, mockApi, renderWithProviders } from "@/test/render";

import { NarrativeTab } from "./NarrativeTab";

const eng = {
  id: "e1",
  client_id: "c1",
  client_name: "Synthetic Demo",
  entity_id: "en1",
  entity_name: "Synthetic Demo",
  name: "FY 2025-26",
  type: "annual",
  period_end: "2026-03-31",
  periods_in_scope_json: [],
  status: "CHECKED",
  partner_id: null,
  manager_id: null,
  due_date: null,
  locked_at: null,
  ai_enabled: true,
  is_quarantine: false,
  merge_required: false,
  version_line: 1,
  legal_hold: false,
  version: 1,
  created_at: "2026-10-01T00:00:00Z",
} as EngagementOut;

const sentence = "The Company has incurred a loss of ₹ 73.18 lakhs during the year ended March 31, 2026.";

describe("Narrative editor", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("highlights quoted figures and rejects formula-like corrections (C6)", async () => {
    const { calls } = mockApi({
      "/api/v1/auth/me": ME,
      "/api/v1/engagements/e1/runs": [{ id: "r1", engagement_id: "e1", kind: "check", status: "succeeded", created_at: "2026-10-20T10:00:00Z", input_version_ids: [], input_hashes: [], params_json: {}, result_json: {} }],
      "/api/v1/runs/r1/result": {
        run_id: "r1",
        key_figures: [],
        statements: {},
        rpt_recon: [],
        xref_map: [],
        summary: {},
        narrative: [{ sheet: "Note 29-34", cell: "A12", sentence, quoted: "₹ 73.18 lakhs", status: "Mismatch", expected_printed: "73.22", metric: "pat" }],
      },
      "/api/v1/engagements/e1/files": [],
      "/api/v1/engagements/e1/narrative-edits": [],
    });
    renderWithProviders(<NarrativeTab eng={eng} can={() => true} />);

    const mark = await screen.findByText("₹ 73.18 lakhs");
    expect(mark.tagName).toBe("MARK");
    expect(mark).toHaveClass("mismatch");

    fireEvent.click(screen.getByRole("button", { name: "Correct" }));
    const textarea = screen.getByLabelText("Corrected text") as HTMLTextAreaElement;
    expect(textarea.value).toContain("₹ 73.22 lakhs");
    const submit = screen.getByRole("button", { name: "Submit for approval" });
    expect(submit).toBeEnabled();

    fireEvent.change(textarea, { target: { value: "=SUM(C4:C6)" } });
    expect(await screen.findByText(/never a formula \(control C6\)/)).toBeInTheDocument();
    expect(submit).toBeDisabled();

    fireEvent.change(textarea, { target: { value: "The Company has incurred a loss of ₹ 73.22 lakhs." } });
    await waitFor(() => expect(submit).toBeEnabled());
    expect(calls.some((c) => c.url.includes("narrative-edits") && c.init?.method === "POST")).toBe(false);
  });

  it("prefills the engine's typed-text proposal when there is one", async () => {
    mockApi({
      "/api/v1/auth/me": ME,
      "/api/v1/engagements/e1/runs": [{ id: "r1", engagement_id: "e1", kind: "check", status: "succeeded", created_at: "2026-10-20T10:00:00Z", input_version_ids: [], input_hashes: [], params_json: {}, result_json: {} }],
      "/api/v1/runs/r1/result": {
        run_id: "r1",
        summary: {},
        narrative: [{ sheet: "Note 29-34", cell: "A12", sentence, quoted: "₹ 73.18 lakhs", status: "Mismatch", expected_printed: "73.22", metric: "pat" }],
        text_proposals: [
          { sheet: "Note 29-34", cell: "A12", new_text: "The Company has incurred a loss of ₹ 73.22 lakhs during the year ended March 31, 2026.", reason: "GC-06 restated" },
          { sheet: "Note 2", cell: "B4", new_text: "Going concern: the net worth is positive.", reason: "GC-01" },
        ],
      },
      "/api/v1/engagements/e1/files": [],
      "/api/v1/engagements/e1/narrative-edits": [],
    });
    renderWithProviders(<NarrativeTab eng={eng} can={() => true} />);

    expect(await screen.findByText("engine proposal")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Correct" }));
    expect((screen.getByLabelText("Corrected text") as HTMLTextAreaElement).value).toBe("The Company has incurred a loss of ₹ 73.22 lakhs during the year ended March 31, 2026.");
    expect((screen.getByLabelText("Reason") as HTMLInputElement).value).toBe("GC-06 restated");

    expect(screen.getByText("Other corrections the engine proposes")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Use" }));
    expect((screen.getByLabelText("Cell") as HTMLInputElement).value).toBe("B4");
    expect((screen.getByLabelText("Corrected text") as HTMLTextAreaElement).value).toBe("Going concern: the net worth is positive.");
  });
});
