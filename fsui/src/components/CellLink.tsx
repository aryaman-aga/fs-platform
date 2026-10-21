import { Link } from "react-router-dom";

import { cellRef } from "@/lib/cells";

/**
 * Every number links to its sheet!cell (08 §4): opens the Checks tab filtered
 * to that sheet and cell, where the evidence and findings for it are listed.
 */
export function CellLink({ engagementId, sheet, cell }: { engagementId?: string; sheet: string | null | undefined; cell: string | null | undefined }) {
  const label = cellRef(sheet, cell);
  if (!engagementId || !sheet) return <span className="mono">{label}</span>;
  const qs = new URLSearchParams({ sheet, ...(cell ? { cell } : {}) }).toString();
  return (
    <Link className="mono" to={`/engagements/${engagementId}/checks?${qs}`} title={`Show ${label}`}>
      {label}
    </Link>
  );
}
