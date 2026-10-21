/**
 * Minimal, lossless editing of scalar values in the engagement configuration
 * YAML (the form editor in Settings). Only `key: scalar` lines of mappings are
 * read or replaced — comments, ordering and everything else are preserved and
 * the server remains the validator (422 on an invalid configuration).
 */

interface Hit {
  line: number;
  indent: number;
  raw: string;
  quote: "'" | '"' | "";
}

const KEY_LINE = /^(\s*)([A-Za-z_][\w-]*):(?:\s+(.*?))?\s*$/;

function unquote(raw: string): { value: string; quote: "'" | '"' | "" } {
  const t = raw.replace(/\s+#.*$/, "").trim();
  if (t.startsWith("'") && t.endsWith("'") && t.length >= 2) return { value: t.slice(1, -1).replace(/''/g, "'"), quote: "'" };
  if (t.startsWith('"') && t.endsWith('"') && t.length >= 2) return { value: t.slice(1, -1).replace(/\\"/g, '"'), quote: '"' };
  return { value: t, quote: "" };
}

function find(lines: string[], path: string[]): Hit | { parentLine: number; childIndent: number } | null {
  let start = 0;
  let end = lines.length;
  let indent = 0;
  for (let depth = 0; depth < path.length; depth += 1) {
    const key = path[depth];
    let found = -1;
    for (let i = start; i < end; i += 1) {
      const m = KEY_LINE.exec(lines[i] ?? "");
      if (!m) continue;
      const ind = (m[1] ?? "").length;
      if (ind !== indent) continue;
      if (m[2] === key) {
        found = i;
        break;
      }
    }
    if (found < 0) {
      if (depth === 0) return null;
      return { parentLine: start - 1, childIndent: indent };
    }
    const m = KEY_LINE.exec(lines[found] ?? "");
    if (depth === path.length - 1) {
      const { quote } = unquote(m?.[3] ?? "");
      return { line: found, indent, raw: m?.[3] ?? "", quote };
    }
    // descend: the block is the following lines indented deeper than this key
    let blockEnd = found + 1;
    let childIndent = indent + 2;
    let firstChild = true;
    while (blockEnd < end) {
      const l = lines[blockEnd] ?? "";
      if (l.trim() === "" || l.trim().startsWith("#")) {
        blockEnd += 1;
        continue;
      }
      const ind = l.length - l.trimStart().length;
      if (ind <= indent) break;
      if (firstChild) {
        childIndent = ind;
        firstChild = false;
      }
      blockEnd += 1;
    }
    start = found + 1;
    end = blockEnd;
    indent = childIndent;
  }
  return null;
}

export function getYamlValue(text: string, path: string): string | null {
  const hit = find(text.split("\n"), path.split("."));
  if (!hit || !("line" in hit)) return null;
  const v = unquote(hit.raw).value;
  return v === "" ? null : v;
}

function render(value: string, quote: "'" | '"' | ""): string {
  if (quote === '"') return `"${value.replace(/"/g, '\\"')}"`;
  if (quote === "'" || /^[\s'"@`%&*!|>?:#[\]{},-]|: | #|^\s|\s$/.test(value) || value === "") return `'${value.replace(/'/g, "''")}'`;
  return value;
}

export function setYamlValue(text: string, path: string, value: string): string {
  const lines = text.split("\n");
  const keys = path.split(".");
  const hit = find(lines, keys);
  const last = keys[keys.length - 1] ?? "";
  if (hit && "line" in hit) {
    const pad = " ".repeat(hit.indent);
    lines[hit.line] = `${pad}${last}: ${render(value, hit.quote)}`;
    return lines.join("\n");
  }
  if (hit && "parentLine" in hit) {
    lines.splice(hit.parentLine + 1, 0, `${" ".repeat(hit.childIndent)}${last}: ${render(value, "")}`);
    return lines.join("\n");
  }
  // missing parent(s): append a new block at the end
  const body = keys.map((k, i) => `${" ".repeat(i * 2)}${k}:${i === keys.length - 1 ? ` ${render(value, "")}` : ""}`).join("\n");
  const trimmed = text.replace(/\s*$/, "");
  return `${trimmed}${trimmed ? "\n" : ""}${body}\n`;
}
