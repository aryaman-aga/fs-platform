/**
 * Control C6 — narrative corrections are typed text, never formulas.
 *
 * Mirrors the backend's FORMULA_LIKE check (fsweb/routers/fixplans.py) so the
 * editor rejects formula-like input before it is sent. The server remains the
 * control; this only gives immediate feedback.
 */

const FORMULA_LIKE =
  /^\s*[=+@]|^\s*-\s*[A-Za-z_$][\w.]*\s*\(|\b(SUM|SUMPRODUCT|ROUND|IF|IFERROR|VLOOKUP|XLOOKUP|TEXT|INDEX|MATCH|INDIRECT|OFFSET|HYPERLINK|CONCATENATE|CONCAT|WEBSERVICE)\s*\(/i;

export function isFormulaLike(text: string): boolean {
  return FORMULA_LIKE.test(text);
}

export interface TextCheck {
  ok: boolean;
  message?: string;
}

/** Validation used by the narrative editor before submitting an edit. */
export function validateNarrativeText(text: string): TextCheck {
  if (!text.trim()) return { ok: false, message: "Enter the corrected text." };
  if (text.length > 32000) return { ok: false, message: "Text is longer than 32,000 characters." };
  if (isFormulaLike(text)) {
    return {
      ok: false,
      message: "Narrative must be typed text, never a formula (control C6). Remove the leading =, +, @ or the function call.",
    };
  }
  return { ok: true };
}

/** A cell reference such as B12 (used for narrative edits: sheet + cell). */
export function isCellAddress(cell: string): boolean {
  return /^[A-Z]{1,3}[1-9]\d{0,6}$/.test(cell);
}
