/** sheet!cell references as Excel prints them ('Note 4-10'!C12). */

export function quoteSheet(sheet: string): string {
  if (/^[A-Za-z_][A-Za-z0-9_.]*$/.test(sheet) && !/^[A-Za-z]{1,3}\d+$/.test(sheet)) return sheet;
  return `'${sheet.replace(/'/g, "''")}'`;
}

export function cellRef(sheet: string | null | undefined, cell: string | null | undefined): string {
  if (!sheet) return cell ?? "";
  if (!cell) return quoteSheet(sheet);
  return `${quoteSheet(sheet)}!${cell}`;
}

export function cellsLabel(sheet: string | null | undefined, cells: unknown[] | null | undefined): string {
  const list = (cells ?? []).map(String).filter(Boolean);
  if (!list.length) return sheet ?? "";
  const shown = list.slice(0, 3).join(", ");
  const more = list.length > 3 ? ` +${list.length - 3}` : "";
  return `${sheet ? quoteSheet(sheet) + "!" : ""}${shown}${more}`;
}
