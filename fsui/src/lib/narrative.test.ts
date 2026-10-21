import { describe, expect, it } from "vitest";

import type { Mention } from "./narrative";
import { classifyStatus, groupMentions, highlightSentence, suggestCorrection } from "./narrative";

const sentence = "The Company earned a profit of ₹ 9.32 lakhs for the quarter and its net worth is ₹ 64.52 lakhs.";
const mentions: Mention[] = [
  { sheet: "Note 29-34", cell: "A12", sentence, quoted: "₹ 9.32 lakhs", status: "Mismatch", expected_printed: "8.23", metric: "pat", period: "Q4_CY" },
  { sheet: "Note 29-34", cell: "A12", sentence, quoted: "₹ 64.52 lakhs", status: "Matched", expected_printed: "64.52", metric: "net_worth" },
];

describe("narrative highlighting", () => {
  it("classifies statuses", () => {
    expect(classifyStatus("Matched")).toBe("matched");
    expect(classifyStatus("Period mismatch")).toBe("mismatch");
    expect(classifyStatus("Claim contradicted")).toBe("mismatch");
    expect(classifyStatus("Unrecognised")).toBe("other");
  });

  it("splits a sentence into plain text and highlighted figures, keeping every character", () => {
    const segs = highlightSentence(sentence, mentions);
    expect(segs.map((s) => s.text).join("")).toBe(sentence);
    const marked = segs.filter((s) => s.status);
    expect(marked.map((s) => [s.text, s.status])).toEqual([
      ["₹ 9.32 lakhs", "mismatch"],
      ["₹ 64.52 lakhs", "matched"],
    ]);
  });

  it("ignores quotes that are not in the sentence", () => {
    const segs = highlightSentence("Nothing quoted here.", mentions);
    expect(segs).toEqual([{ text: "Nothing quoted here." }]);
  });

  it("groups mentions by cell and sentence and counts mismatches", () => {
    const blocks = groupMentions(mentions);
    expect(blocks).toHaveLength(1);
    expect(blocks[0]?.key).toBe("Note 29-34!A12");
    expect(blocks[0]?.sentences).toHaveLength(1);
    expect(blocks[0]?.mismatches).toBe(1);
  });

  it("suggests typed-text wording with the statements' figure, same structure", () => {
    expect(suggestCorrection(sentence, mentions)).toBe(
      "The Company earned a profit of ₹ 8.23 lakhs for the quarter and its net worth is ₹ 64.52 lakhs.",
    );
  });
});
