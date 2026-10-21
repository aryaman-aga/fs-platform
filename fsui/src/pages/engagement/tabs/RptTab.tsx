/** Related parties: three-way reconciliation RPT note / trial balance / FS note (RPT-09). */
import { useMemo, useState } from "react";

import { latestSucceeded, useRunResult, useRuns } from "@/api/hooks";
import type { RptRow } from "@/api/types";
import { Amount } from "@/components/Prefs";
import { CellLink } from "@/components/CellLink";
import { DataGrid } from "@/components/DataGrid";
import { Card, Chip, EmptyState, Notice, Spinner } from "@/components/ui";
import { titleCase } from "@/lib/format";

import type { TabProps } from "../tabKeys";

function splitRef(ref: string | null | undefined): [string | null, string | null] {
  if (!ref) return [null, null];
  const i = ref.lastIndexOf("!");
  if (i < 0) return [null, ref];
  return [ref.slice(0, i).replace(/^'(.*)'$/, "$1").replace(/''/g, "'"), ref.slice(i + 1)];
}

export function RptTab({ eng }: TabProps) {
  const runs = useRuns(eng.id, "check");
  const latest = latestSucceeded(runs.data, "check");
  const result = useRunResult(latest?.id);
  const rows = useMemo(() => (result.data?.rpt_recon ?? []) as RptRow[], [result.data]);
  const [onlyIssues, setOnlyIssues] = useState(true);
  const [period, setPeriod] = useState("");
  const periods = useMemo(() => [...new Set(rows.map((r) => r.period))].sort(), [rows]);
  const shown = rows.filter((r) => (!onlyIssues || r.status !== "Agreed" || r.flags.length > 0) && (!period || r.period === period));
  const issues = rows.filter((r) => r.status !== "Agreed").length;

  if (!latest) return <Notice>Run checks to build the trial-balance driven related-party ledger (RPT-08) and the three-way grid.</Notice>;
  if (result.isLoading) return <Spinner />;
  return (
    <div className="stack">
      <div className="row between">
        <p className="muted" style={{ margin: 0 }}>
          Each counterparty × nature × period is reconciled between the RPT note, the trial balance and the financial-statement note line.
        </p>
        <div className="row">
          <Chip tone={issues ? "danger" : "ok"}>{issues} difference(s)</Chip>
          <label className="checkbox">
            <input type="checkbox" checked={onlyIssues} onChange={(e) => setOnlyIssues(e.target.checked)} />
            <span>Only differences / flags</span>
          </label>
          <select aria-label="Period" value={period} onChange={(e) => setPeriod(e.target.value)} style={{ width: "auto" }}>
            <option value="">All periods</option>
            {periods.map((p) => (
              <option key={p} value={p}>
                {p.replace("_", " ")}
              </option>
            ))}
          </select>
        </div>
      </div>
      <Card bodyClass="">
        {rows.length === 0 ? (
          <EmptyState>No related-party data was found in this workbook.</EmptyState>
        ) : (
          <DataGrid
            label="Related-party three-way reconciliation"
            rows={shown}
            rowKey={(r) => `${r.counterparty}|${r.nature}|${r.period}`}
            empty="Everything agrees."
            columns={[
              { key: "cp", header: "Counterparty", className: "wrap", render: (r) => <span>{r.counterparty}{r.rpt_party_as_written && r.rpt_party_as_written !== r.counterparty ? <span className="small muted"> (RPT note: {r.rpt_party_as_written})</span> : null}</span>, sortValue: (r) => r.counterparty },
              { key: "nature", header: "Nature", render: (r) => titleCase(r.nature), sortValue: (r) => r.nature },
              { key: "period", header: "Period", render: (r) => r.period.replace("_", " "), sortValue: (r) => r.period },
              {
                key: "rpt",
                header: "RPT note",
                className: "num",
                render: (r) => {
                  const [s, c] = splitRef(r.rpt_cell);
                  return (
                    <div>
                      <Amount value={r.rpt_value} />
                      {r.rpt_cell ? <div className="small"><CellLink engagementId={eng.id} sheet={s} cell={c} /></div> : null}
                    </div>
                  );
                },
              },
              { key: "tb", header: "Trial balance", className: "num", render: (r) => <div><Amount value={r.tb_value} />{r.tb_accounts.length ? <div className="small muted">{r.tb_accounts.slice(0, 3).join(", ")}{r.tb_accounts.length > 3 ? "…" : ""}</div> : null}</div> },
              {
                key: "fs",
                header: "FS note",
                className: "num",
                render: (r) => {
                  const [s, c] = splitRef(r.fs_cell);
                  return (
                    <div>
                      <Amount value={r.fs_value} />
                      {r.fs_cell ? <div className="small"><CellLink engagementId={eng.id} sheet={s} cell={c} /></div> : null}
                    </div>
                  );
                },
              },
              { key: "var", header: "Variance", className: "num", render: (r) => <strong style={{ color: r.variance && Number(r.variance) !== 0 ? "var(--danger)" : undefined }}><Amount value={r.variance} /></strong>, sortValue: (r) => Math.abs(Number(r.variance ?? 0)) },
              { key: "status", header: "Status", render: (r) => <Chip tone={r.status === "Agreed" ? "ok" : "danger"}>{r.status}</Chip>, sortValue: (r) => r.status },
              { key: "flags", header: "Flags", className: "wrap", render: (r) => <span className="row">{r.flags.map((f) => <Chip key={f} tone="warn">{titleCase(f)}</Chip>)}</span> },
            ]}
          />
        )}
      </Card>
    </div>
  );
}
