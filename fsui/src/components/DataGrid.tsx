import type { KeyboardEvent, ReactNode } from "react";
import { useMemo, useRef, useState } from "react";

export interface Column<T> {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  /** Value used for sorting (omit to make the column unsortable). */
  sortValue?: (row: T) => string | number;
  className?: string;
  width?: number | string;
}

/**
 * Lean data grid with keyboard navigation: ↑/↓ move between rows, Home/End jump,
 * Enter or Space opens the focused row. Column headers sort when they have a
 * sort value. Used for findings, versions, audit events and admin lists.
 */
export function DataGrid<T>({
  rows,
  columns,
  rowKey,
  onOpen,
  selectedKey,
  label,
  empty,
  rowClassName,
  maxHeight,
}: {
  rows: T[];
  columns: Column<T>[];
  rowKey: (row: T) => string;
  onOpen?: (row: T) => void;
  selectedKey?: string | null;
  label: string;
  empty?: ReactNode;
  rowClassName?: (row: T) => string | undefined;
  maxHeight?: number;
}) {
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 } | null>(null);
  const bodyRef = useRef<HTMLTableSectionElement>(null);
  const sorted = useMemo(() => {
    if (!sort) return rows;
    const col = columns.find((c) => c.key === sort.key);
    if (!col?.sortValue) return rows;
    const sv = col.sortValue;
    return [...rows].sort((a, b) => {
      const x = sv(a);
      const y = sv(b);
      if (typeof x === "number" && typeof y === "number") return (x - y) * sort.dir;
      return String(x).localeCompare(String(y), "en", { numeric: true }) * sort.dir;
    });
  }, [rows, columns, sort]);

  const focusRow = (i: number) => {
    const el = bodyRef.current?.querySelectorAll<HTMLTableRowElement>("tr[data-row]")[i];
    el?.focus();
  };
  const onKey = (e: KeyboardEvent<HTMLTableRowElement>, i: number, row: T) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      focusRow(Math.min(i + 1, sorted.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      focusRow(Math.max(i - 1, 0));
    } else if (e.key === "Home") {
      e.preventDefault();
      focusRow(0);
    } else if (e.key === "End") {
      e.preventDefault();
      focusRow(sorted.length - 1);
    } else if ((e.key === "Enter" || e.key === " ") && onOpen) {
      e.preventDefault();
      onOpen(row);
    }
  };

  if (!rows.length) return <div className="empty">{empty ?? "Nothing to show."}</div>;
  return (
    <div className="table-wrap grid-scroll" style={maxHeight ? { maxHeight } : undefined}>
      <table className="data" aria-label={label}>
        <thead>
          <tr>
            {columns.map((c) => {
              const active = sort?.key === c.key;
              const ariaSort = active ? (sort?.dir === 1 ? "ascending" : "descending") : undefined;
              return (
                <th
                  key={c.key}
                  scope="col"
                  className={`${c.className ?? ""} ${c.sortValue ? "sortable" : ""}`}
                  style={c.width ? { width: c.width } : undefined}
                  aria-sort={ariaSort}
                  onClick={c.sortValue ? () => setSort((s) => (s?.key === c.key ? { key: c.key, dir: s.dir === 1 ? -1 : 1 } : { key: c.key, dir: 1 })) : undefined}
                >
                  {c.header}
                  {active ? <span aria-hidden="true"> {sort?.dir === 1 ? "▲" : "▼"}</span> : null}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody ref={bodyRef}>
          {sorted.map((row, i) => {
            const k = rowKey(row);
            return (
              <tr
                key={k}
                data-row
                tabIndex={onOpen ? 0 : undefined}
                className={[k === selectedKey ? "selected" : "", rowClassName?.(row) ?? ""].join(" ").trim() || undefined}
                aria-selected={onOpen ? k === selectedKey : undefined}
                onClick={onOpen ? () => onOpen(row) : undefined}
                onKeyDown={onOpen ? (e) => onKey(e, i, row) : undefined}
                style={onOpen ? { cursor: "pointer" } : undefined}
              >
                {columns.map((c) => (
                  <td key={c.key} className={c.className}>
                    {c.render(row)}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
