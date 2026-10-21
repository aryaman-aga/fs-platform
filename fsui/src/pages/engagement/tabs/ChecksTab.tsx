/** Checks: run the engine, filter findings, inspect evidence, set status, link to fixes / review notes. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { api, apiBlob, saveFile } from "@/api/client";
import { latestSucceeded, qk, useFindings, useFixPlans, useRunResult, useRuns } from "@/api/hooks";
import { newIdempotencyKey } from "@/api/runs";
import type { ArtefactOut, Evidence, FindingOut, KeyFigure, RunOut } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { Amount } from "@/components/Prefs";
import { DataGrid } from "@/components/DataGrid";
import { RunWatcher } from "@/components/RunWatcher";
import { useToast } from "@/components/Toast";
import { Button, Card, Checkbox, Chip, EmptyState, ErrorNotice, KeyValue, Notice, SelectInput, SeverityBadge, Spinner, Stat, TextArea } from "@/components/ui";
import { cellRef, cellsLabel } from "@/lib/cells";
import { FINDING_STATUSES, SEVERITIES, countBySeverity, distinctSheets, filterFindings, sortFindings, statusChangeError } from "@/lib/findings";
import { formatDateTime, titleCase } from "@/lib/format";

import type { TabProps } from "../tabKeys";

export function ChecksTab({ eng, can }: TabProps) {
  const qc = useQueryClient();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const runs = useRuns(eng.id, "check");
  const [runId, setRunId] = useState<string | null>(null);
  const [watch, setWatch] = useState<string | null>(null);
  const latest = latestSucceeded(runs.data, "check");
  const selectedRun = (runs.data ?? []).find((r) => r.id === runId) ?? latest;
  const findings = useFindings(selectedRun?.status === "succeeded" ? selectedRun.id : undefined);
  const result = useRunResult(selectedRun?.status === "succeeded" ? selectedRun.id : undefined);
  const inFlight = (runs.data ?? []).find((r) => r.status === "queued" || r.status === "running");
  const finished = useRef(new Set<string>()); // runs already reported, while the run list still shows them in flight

  useEffect(() => {
    if (inFlight && !watch && !finished.current.has(inFlight.id)) setWatch(inFlight.id);
  }, [inFlight, watch]);

  const start = useMutation({
    mutationFn: () => api<RunOut>(`/engagements/${eng.id}/runs`, { method: "POST", body: { kind: "check", idempotency_key: newIdempotencyKey("check") } }),
    onSuccess: (r) => {
      setWatch(r.id);
      void qc.invalidateQueries({ queryKey: qk.runs(eng.id, "check") });
    },
    onError: (e) => toast.error(e, "Checks not started"),
  });

  const [severity, setSeverity] = useState<string[]>([]);
  const [status, setStatus] = useState<string[]>(["Open", "Accepted"]);
  const [rule, setRule] = useState("");
  const sheetFilter = params.get("sheet") ?? "";
  const cellFilter = params.get("cell") ?? "";
  const [text, setText] = useState(cellFilter);
  const [selected, setSelected] = useState<FindingOut | null>(null);

  const all = useMemo(() => sortFindings(findings.data ?? []), [findings.data]);
  const shown = useMemo(
    () => filterFindings(all, { severity, status, rule, sheet: sheetFilter || undefined, text }),
    [all, severity, status, rule, sheetFilter, text],
  );
  const counts = countBySeverity(all.filter((f) => f.status !== "Resolved" && f.status !== "Not an issue"));
  const sheets = distinctSheets(all);
  const summary = result.data?.summary as { selftest?: { matched?: number; evaluated?: number }; warnings?: string[]; pdf?: { misses: number; stale: boolean } | null } | undefined;

  return (
    <div className="stack">
      <div className="row between">
        <div className="row">
          {can("run.check") ? (
            <Button variant="primary" onClick={() => start.mutate()} busy={start.isPending} disabled={!!inFlight} data-testid="run-checks">
              Run checks
            </Button>
          ) : null}
          {(runs.data ?? []).length > 1 ? (
            <SelectInput
              label="Check run"
              value={selectedRun?.id ?? ""}
              onChange={(e) => setRunId(e.target.value)}
              options={(runs.data ?? []).map((r) => ({ value: r.id, label: `${formatDateTime(r.created_at)} — ${r.status}` }))}
            />
          ) : null}
        </div>
        {selectedRun ? <RunMeta run={selectedRun} /> : null}
      </div>
      {watch ? (
        <RunWatcher
          runId={watch}
          title="Running checks"
          onDone={(r) => {
            finished.current.add(r.id);
            setWatch(null);
            setRunId(r.id);
            void qc.invalidateQueries({ queryKey: ["engagement", eng.id] });
            if (r.status === "succeeded") toast.ok("Checks finished");
          }}
        />
      ) : null}
      {!selectedRun && !runs.isLoading ? <Notice>No successful check run yet. Upload a workbook and run checks.</Notice> : null}
      {selectedRun ? (
        <>
          <div className="grid-4">
            {SEVERITIES.map((s) => (
              <Stat key={s} label={`${s} (open)`} value={counts[s]} tone={s === "High" && counts[s] ? "danger" : s === "Medium" && counts[s] ? "warn" : undefined} />
            ))}
          </div>
          {summary?.warnings?.length ? (
            <Notice tone="warn">
              {summary.warnings.slice(0, 5).map((w) => (
                <div key={w}>{w}</div>
              ))}
            </Notice>
          ) : null}
          <div className="split">
            <Card
              title={`Findings (${shown.length} of ${all.length})`}
              bodyClass="card-body stack"
              actions={<Artefacts runId={selectedRun.id} />}
            >
              <div className="row" role="group" aria-label="Filters">
                <fieldset className="row" style={{ border: "none", padding: 0, margin: 0 }}>
                  <legend className="sr-only">Severity</legend>
                  {SEVERITIES.map((s) => (
                    <Checkbox key={s} label={s} checked={severity.includes(s)} onChange={(e) => setSeverity((xs) => (e.target.checked ? [...xs, s] : xs.filter((x) => x !== s)))} />
                  ))}
                </fieldset>
                <select aria-label="Rule family" value={rule} onChange={(e) => setRule(e.target.value)} style={{ width: "auto" }}>
                  <option value="">All rules</option>
                  {[...new Set(all.map((f) => f.rule_id.split("-")[0] ?? ""))].sort().map((r) => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </select>
                <select
                  aria-label="Sheet"
                  value={sheetFilter}
                  onChange={(e) => {
                    const next = new URLSearchParams(params);
                    if (e.target.value) next.set("sheet", e.target.value);
                    else next.delete("sheet");
                    next.delete("cell");
                    setParams(next, { replace: true });
                  }}
                  style={{ width: "auto" }}
                >
                  <option value="">All sheets</option>
                  {sheets.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
                <select
                  aria-label="Status"
                  value={status.join(",")}
                  onChange={(e) => setStatus(e.target.value ? e.target.value.split(",") : [])}
                  style={{ width: "auto" }}
                >
                  <option value="Open,Accepted">Open & accepted</option>
                  <option value="">Any status</option>
                  {FINDING_STATUSES.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
                <input type="search" placeholder="Search caption, cell, rule…" aria-label="Search findings" value={text} onChange={(e) => setText(e.target.value)} style={{ maxWidth: 240 }} />
              </div>
              {findings.isLoading ? (
                <Spinner label="Loading findings" />
              ) : findings.error ? (
                <ErrorNotice error={findings.error} />
              ) : (
                <DataGrid
                  label="Findings"
                  rows={shown}
                  rowKey={(f) => f.id}
                  onOpen={(f) => setSelected(f)}
                  selectedKey={selected?.id}
                  empty={<EmptyState>{all.length ? "No finding matches the filters." : "No findings — every rule passed."}</EmptyState>}
                  columns={[
                    {
                      key: "fid",
                      header: "Finding",
                      className: "nowrap",
                      render: (f) => (
                        <>
                          <span className="mono">{f.fid}</span>
                          <div className="mono small muted">{f.rule_id}</div>
                        </>
                      ),
                      sortValue: (f) => f.fid,
                    },
                    { key: "sev", header: "Severity", render: (f) => <SeverityBadge severity={f.severity} />, sortValue: (f) => ["High", "Medium", "Low", "Info"].indexOf(f.severity) },
                    { key: "where", header: "Where", render: (f) => <span className="mono small">{cellsLabel(f.sheet, f.cells)}</span>, sortValue: (f) => `${f.sheet}` },
                    { key: "cap", header: "Caption / issue", className: "wrap", render: (f) => <span>{f.caption || f.category}</span> },
                    { key: "printed", header: "Printed", className: "num", render: (f) => f.printed ?? "" },
                    { key: "diff", header: "Diff", className: "num", render: (f) => (f.diff ? <Amount value={f.diff} /> : "") },
                    { key: "status", header: "Status", render: (f) => <Chip tone={f.status === "Open" ? "warn" : f.status === "Resolved" ? "ok" : undefined}>{f.status}</Chip>, sortValue: (f) => f.status },
                  ]}
                />
              )}
            </Card>
            <div className="side-panel stack">
              {selected ? (
                <FindingPanel key={selected.id} eng={eng} can={can} finding={selected} onChanged={(f) => setSelected(f)} />
              ) : (
                <Card title="Finding details">
                  <EmptyState>Select a finding (↑/↓ then Enter) to see its evidence and actions.</EmptyState>
                </Card>
              )}
              <KeyFigures figures={(result.data?.key_figures ?? []) as KeyFigure[]} engagementId={eng.id} />
              {summary?.selftest ? (
                <Card title="Engine self-test">
                  <p className="small">
                    {summary.selftest.matched ?? 0} of {summary.selftest.evaluated ?? 0} print-sheet formulas re-evaluate to Excel's cached values (≥ 99.5 % required before fixes).
                  </p>
                </Card>
              ) : null}
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}

function RunMeta({ run }: { run: RunOut }) {
  return (
    <span className="small muted">
      Run {formatDateTime(run.finished_at ?? run.created_at)} · engine {run.engine_version ?? "?"} · rules {run.rule_set_version ?? "?"}
    </span>
  );
}

function Artefacts({ runId }: { runId: string }) {
  const toast = useToast();
  const arts = useQuery({ queryKey: qk.artefacts(runId), queryFn: () => api<ArtefactOut[]>(`/runs/${runId}/artefacts`) });
  const items = (arts.data ?? []).filter((a) => a.kind === "register" || a.kind === "html");
  return (
    <span className="row">
      {items.map((a) => (
        <Button
          key={a.id}
          size="sm"
          onClick={async () => {
            try {
              saveFile(await apiBlob(`/artefacts/${a.id}/download`, {}, a.name));
            } catch (e) {
              toast.error(e, "Download refused");
            }
          }}
        >
          ⤓ {a.kind === "register" ? "Exceptions register" : "HTML report"}
        </Button>
      ))}
    </span>
  );
}

function KeyFigures({ figures, engagementId }: { figures: KeyFigure[]; engagementId: string }) {
  const periods = [...new Set(figures.map((f) => f.period))].sort();
  const keys = [...new Set(figures.map((f) => f.key))];
  if (!figures.length) return null;
  const by = new Map(figures.map((f) => [`${f.key}@${f.period}`, f]));
  return (
    <Card title="Key figures" bodyClass="">
      <div className="table-wrap" style={{ border: "none" }}>
        <table className="data">
          <thead>
            <tr>
              <th scope="col">Figure</th>
              {periods.map((p) => (
                <th key={p} scope="col" className="num">
                  {p.replace("_", " ")}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {keys.map((k) => {
              const any = figures.find((f) => f.key === k);
              return (
                <tr key={k}>
                  <td>{any?.label ?? titleCase(k)}</td>
                  {periods.map((p) => {
                    const f = by.get(`${k}@${p}`);
                    return (
                      <td key={p} className="num">
                        {f ? (
                          f.ref ? (
                            <Link to={`/engagements/${engagementId}/checks?sheet=${encodeURIComponent(f.ref.sheet)}&cell=${f.ref.addr}`} title={cellRef(f.ref.sheet, f.ref.addr)}>
                              <Amount value={f.value} />
                            </Link>
                          ) : (
                            <Amount value={f.value} />
                          )
                        ) : (
                          ""
                        )}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function FindingPanel({ eng, can, finding, onChanged }: TabProps & { finding: FindingOut; onChanged: (f: FindingOut) => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const { me } = useAuth();
  const navigate = useNavigate();
  const plans = useFixPlans(eng.id);
  const [status, setStatus] = useState(finding.status);
  const [reason, setReason] = useState(finding.status_reason ?? "");
  const [comment, setComment] = useState(finding.comment ?? "");
  const [noteText, setNoteText] = useState("");
  const statusError = statusChangeError(status, reason);
  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api<FindingOut>(`/findings/${finding.id}`, { method: "PATCH", body, ifMatch: `W/"${finding.version}"` }),
    onSuccess: async (f) => {
      onChanged(f);
      toast.ok("Finding updated");
      await qc.invalidateQueries({ queryKey: qk.findings(f.run_id) });
    },
    onError: (e) => toast.error(e, "Not updated"),
  });
  const raise = useMutation({
    mutationFn: () =>
      api(`/engagements/${eng.id}/review-notes`, { method: "POST", body: { target_type: "finding", target_ref: finding.id, text: noteText } }),
    onSuccess: async () => {
      setNoteText("");
      toast.ok("Review note raised");
      await qc.invalidateQueries({ queryKey: qk.notes(eng.id) });
    },
    onError: (e) => toast.error(e, "Note not raised"),
  });
  const evidence = (finding.evidence_json ?? []) as Evidence[];
  const fixes = (plans.data ?? [])
    .filter((p) => !["superseded", "rejected"].includes(p.status))
    .flatMap((p) => (p.edits ?? []).filter((e) => e.sheet === finding.sheet && finding.cells.map(String).includes(e.cell)).map((e) => ({ p, e })));

  return (
    <Card
      title={
        <div className="row">
          <SeverityBadge severity={finding.severity} />
          <h2 style={{ margin: 0 }}>
            {finding.fid} · {finding.rule_id}
          </h2>
        </div>
      }
    >
      <div className="stack">
        <p style={{ margin: 0 }}>{finding.explanation}</p>
        <KeyValue
          items={[
            ["Where", <span className="mono" key="w">{cellsLabel(finding.sheet, finding.cells)}</span>],
            ["Caption", finding.caption],
            ["Printed", finding.printed],
            ["Expected", finding.expected],
            ["Difference", finding.diff ? <Amount key="d" value={finding.diff} /> : null],
            ["Period", finding.period],
            ["Certainty", finding.certainty],
            ["Fix kind", titleCase(finding.fix_kind)],
            ["Category", finding.category],
          ]}
        />
        {finding.suggested_fix ? <Notice>Suggested fix: {finding.suggested_fix}</Notice> : null}
        {evidence.length ? (
          <div className="table-wrap" style={{ maxHeight: 260 }}>
            <table className="data" aria-label="Evidence">
              <thead>
                <tr>
                  <th>Evidence</th>
                  <th>Cell</th>
                  <th className="num">Printed</th>
                </tr>
              </thead>
              <tbody>
                {evidence.map((ev, i) => (
                  <tr key={i}>
                    <td className="wrap">
                      {ev.label}
                      {ev.note ? <div className="small muted">{ev.note}</div> : null}
                    </td>
                    <td className="mono small">{ev.ref ? cellRef(ev.ref.sheet, ev.ref.addr) : ""}</td>
                    <td className="num">{ev.printed ?? (ev.value !== null && ev.value !== undefined ? <Amount value={ev.value} /> : "")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        <div className="stack" style={{ gap: 6 }}>
          <strong className="small">Linked fixes</strong>
          {fixes.length ? (
            fixes.map(({ p, e }) => (
              <Link key={`${p.id}-${e.id}`} to={`/engagements/${eng.id}/fix`} className="small">
                {e.eid} · {titleCase(e.grp)} · plan {p.status}
              </Link>
            ))
          ) : (
            <span className="small muted">
              No fix item yet.{" "}
              {can("fix.propose") ? <Link to={`/engagements/${eng.id}/fix`}>Propose a fix plan</Link> : null}
            </span>
          )}
        </div>
        {can("finding.update") ? (
          <div className="stack" style={{ gap: 8 }}>
            <div className="divider" />
            <SelectInput label="Status" value={status} onChange={(e) => setStatus(e.target.value)} options={[...FINDING_STATUSES]} />
            {status === "Not an issue" ? (
              <TextArea label="Reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} error={statusError ?? undefined} />
            ) : null}
            <TextArea label="Comment" value={comment} onChange={(e) => setComment(e.target.value)} style={{ minHeight: 60 }} />
            <div className="row">
              <Button
                variant="primary"
                busy={patch.isPending}
                disabled={!!statusError}
                onClick={() => patch.mutate({ status, status_reason: status === "Not an issue" ? reason : null, comment })}
              >
                Save
              </Button>
              <Button onClick={() => patch.mutate({ assigned_to: me?.user.id })}>Assign to me</Button>
              {eng.manager_id ? <Button onClick={() => patch.mutate({ assigned_to: eng.manager_id })}>Assign to manager</Button> : null}
              {can("release.client") ? (
                <Button onClick={() => patch.mutate({ shared_with_client: !finding.shared_with_client })}>
                  {finding.shared_with_client ? "Unshare with client" : "Share with client"}
                </Button>
              ) : null}
            </div>
          </div>
        ) : null}
        {can("review.raise") ? (
          <div className="stack" style={{ gap: 6 }}>
            <div className="divider" />
            <TextArea label="Raise a review note on this finding" value={noteText} onChange={(e) => setNoteText(e.target.value)} style={{ minHeight: 60 }} />
            <div className="row">
              <Button onClick={() => raise.mutate()} busy={raise.isPending} disabled={!noteText.trim()}>
                Raise note
              </Button>
              <Button variant="ghost" onClick={() => navigate(`/engagements/${eng.id}/notes`)}>
                Open review notes
              </Button>
            </div>
          </div>
        ) : null}
      </div>
    </Card>
  );
}
