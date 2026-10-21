/**
 * Narrative (Going Concern etc.) highlighting: quoted figures are shown green
 * when they match the statements and red when they do not (08 §3 Narrative tab).
 *
 * The engine publishes one record per mention (sheet, cell, sentence, quoted
 * text, status, expected …). Field names vary slightly between engine
 * versions, so the accessors below are tolerant.
 */

export type MentionStatus = "matched" | "mismatch" | "other";

export interface Mention {
  sheet?: string | null;
  cell?: string | null;
  sentence?: string | null;
  text?: string | null;
  quoted?: string | null;
  quoted_text?: string | null;
  raw?: string | null;
  status?: string | null;
  metric?: string | null;
  period?: string | null;
  value?: string | number | null;
  expected?: string | number | null;
  expected_printed?: string | null;
  [key: string]: unknown;
}

export interface Segment {
  text: string;
  status?: MentionStatus;
  mention?: Mention;
}

export function mentionQuote(m: Mention): string {
  return String(m.quoted ?? m.quoted_text ?? m.raw ?? m.text ?? "").trim();
}

export function mentionSentence(m: Mention): string {
  return String(m.sentence ?? "").trim();
}

export function classifyStatus(status: string | null | undefined): MentionStatus {
  const s = (status ?? "").toLowerCase();
  if (s === "matched" || s === "match" || s === "ok") return "matched";
  if (!s || s === "unrecognised" || s === "unrecognized") return "other";
  return "mismatch";
}

/**
 * Split a sentence into plain and highlighted segments. Each mention's quoted
 * text is located in order of appearance; overlapping or missing quotes are
 * ignored so the sentence is always rendered in full.
 */
export function highlightSentence(sentence: string, mentions: Mention[]): Segment[] {
  const found: { start: number; end: number; m: Mention }[] = [];
  let cursor = 0;
  const withPos = mentions
    .map((m) => ({ m, q: mentionQuote(m) }))
    .filter((x) => x.q.length > 0);
  for (const { m, q } of withPos) {
    let idx = sentence.indexOf(q, cursor);
    if (idx < 0) idx = sentence.indexOf(q);
    if (idx < 0) continue;
    const end = idx + q.length;
    if (found.some((f) => idx < f.end && end > f.start)) continue;
    found.push({ start: idx, end, m });
    cursor = end;
  }
  found.sort((a, b) => a.start - b.start);
  const out: Segment[] = [];
  let pos = 0;
  for (const f of found) {
    if (f.start > pos) out.push({ text: sentence.slice(pos, f.start) });
    out.push({ text: sentence.slice(f.start, f.end), status: classifyStatus(f.m.status), mention: f.m });
    pos = f.end;
  }
  if (pos < sentence.length) out.push({ text: sentence.slice(pos) });
  return out;
}

export interface NarrativeBlock {
  key: string;
  sheet: string;
  cell: string;
  sentences: { sentence: string; mentions: Mention[] }[];
  mismatches: number;
}

/** Group mentions by sheet!cell, then by sentence (keeping the engine's order). */
export function groupMentions(mentions: Mention[]): NarrativeBlock[] {
  const blocks = new Map<string, NarrativeBlock>();
  for (const m of mentions) {
    const sheet = String(m.sheet ?? "");
    const cell = String(m.cell ?? "");
    const key = `${sheet}!${cell}`;
    let b = blocks.get(key);
    if (!b) {
      b = { key, sheet, cell, sentences: [], mismatches: 0 };
      blocks.set(key, b);
    }
    const sentence = mentionSentence(m) || mentionQuote(m);
    let s = b.sentences.find((x) => x.sentence === sentence);
    if (!s) {
      s = { sentence, mentions: [] };
      b.sentences.push(s);
    }
    s.mentions.push(m);
    if (classifyStatus(m.status) === "mismatch") b.mismatches += 1;
  }
  return [...blocks.values()];
}

/**
 * Suggest corrected wording: replace each mismatched quoted figure with the
 * value the statements print, keeping the sentence structure (GC-06). The
 * result is typed text; a person reviews and submits it for approval.
 */
export function suggestCorrection(sentence: string, mentions: Mention[]): string {
  let out = sentence;
  for (const m of mentions) {
    if (classifyStatus(m.status) !== "mismatch") continue;
    const q = mentionQuote(m);
    const expected = m.expected_printed ?? (m.expected !== null && m.expected !== undefined ? String(m.expected) : "");
    if (!q || !expected) continue;
    const numberPart = /[\d][\d,]*(?:\.\d+)?/.exec(q);
    if (!numberPart) continue;
    const replacement = q.replace(numberPart[0], String(expected).replace(/^\((.*)\)$/, "$1"));
    out = out.replace(q, replacement);
  }
  return out;
}
