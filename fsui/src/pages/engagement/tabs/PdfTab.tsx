/** PDF: render draft / final, reconcile with the workbook (PDF-01, C7), view, release to the client (C12). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Suspense, lazy, useEffect, useState } from "react";

import { api, apiBlob, saveFile } from "@/api/client";
import { qk, useGates, useRuns } from "@/api/hooks";
import { newIdempotencyKey } from "@/api/runs";
import type { PdfRecon, ReleaseOut, RunOut } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { DataGrid } from "@/components/DataGrid";
import { RunWatcher } from "@/components/RunWatcher";
import { useToast } from "@/components/Toast";
import { Button, Card, Chip, EmptyState, ErrorNotice, Notice, RunStatusChip, SelectInput, Spinner, TextArea } from "@/components/ui";
import { cellRef } from "@/lib/cells";
import { formatDateTime } from "@/lib/format";

import type { TabProps } from "../tabKeys";

// pdf.js is large: load it only when a PDF is shown
const PdfViewer = lazy(() => import("@/components/PdfViewer").then((m) => ({ default: m.PdfViewer })));

interface RenderResult {
  mode?: string;
  engine?: string;
  engine_version?: string;
  pages?: number;
  pdf_version_id?: string;
  misses?: number;
  verification?: PdfRecon | null;
}

export function PdfTab({ eng, can }: TabProps) {
  const qc = useQueryClient();
  const toast = useToast();
  const { me } = useAuth();
  const runs = useRuns(eng.id, "render");
  const gates = useGates(eng.id);
  const releases = useQuery({ queryKey: qk.releases(eng.id), queryFn: () => api<ReleaseOut[]>(`/engagements/${eng.id}/releases`) });
  const [engine, setEngine] = useState("auto");
  const [watch, setWatch] = useState<string | null>(null);
  const [selected, setSelected] = useState<RunOut | null>(null);
  const [pdf, setPdf] = useState<ArrayBuffer | null>(null);
  const [notes, setNotes] = useState("");
  const succeeded = (runs.data ?? []).filter((r) => r.status === "succeeded");
  const current = selected ?? succeeded[0] ?? null;
  const lastRun = (runs.data ?? [])[0];
  const lastFailed = lastRun?.status === "failed" ? lastRun : null;
  const result = (current?.result_json ?? {}) as RenderResult;
  const finalGates = gates.data?.final_pdf ?? [];
  const finalBlocked = finalGates.filter((g) => !g.ok);

  const render = useMutation({
    mutationFn: (mode: "draft" | "final") =>
      api<RunOut>(`/engagements/${eng.id}/render`, { method: "POST", body: { mode, engine, idempotency_key: newIdempotencyKey(`render-${mode}`) } }),
    onSuccess: (r) => setWatch(r.id),
    onError: (e) => toast.error(e, "Render refused"),
  });
  const release = useMutation({
    mutationFn: () => api<ReleaseOut>(`/engagements/${eng.id}/release`, { method: "POST", body: { render_run_id: current?.id, notes } }),
    onSuccess: async () => {
      toast.ok("Draft released to the client");
      setNotes("");
      await qc.invalidateQueries({ queryKey: ["engagement", eng.id] });
    },
    onError: (e) => toast.error(e, "Not released"),
  });

  useEffect(() => {
    let cancelled = false;
    setPdf(null);
    const vid = current?.output_version_id;
    if (!vid || !can("file.download")) return;
    apiBlob(`/versions/${vid}/download`, {}, "statements.pdf")
      .then((f) => f.blob.arrayBuffer())
      .then((buf) => {
        if (!cancelled) setPdf(buf);
      })
      .catch((e: unknown) => {
        if (!cancelled) toast.error(e, "Cannot load the PDF");
      });
    return () => {
      cancelled = true;
    };
    // toast is stable; reload only when the selected render changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current?.output_version_id]);

  const misses = result.verification?.misses ?? [];
  return (
    <div className="stack">
      <div className="split">
        <Card
          title="Render"
          actions={
            <>
              {can("render.draft") ? (
                <Button variant="primary" onClick={() => render.mutate("draft")} busy={render.isPending} data-testid="render-draft">
                  Render draft PDF
                </Button>
              ) : null}
              {can("release.client") ? (
                <Button onClick={() => render.mutate("final")} disabled={finalBlocked.length > 0 || render.isPending} title={finalBlocked.map((g) => g.label).join("; ")}>
                  Render FINAL
                </Button>
              ) : null}
            </>
          }
        >
          <div className="stack">
            <SelectInput
              label="Engine"
              value={engine}
              onChange={(e) => setEngine(e.target.value)}
              hint="Auto: Windows render worker with Excel when available, else LibreOffice, else the built-in renderer (drafts)."
              options={[
                { value: "auto", label: "Auto" },
                { value: "excel", label: "Microsoft Excel (render worker)" },
                { value: "libreoffice", label: "LibreOffice" },
                { value: "native", label: "Built-in renderer (drafts)" },
              ]}
            />
            <div>
              <strong className="small">FINAL PDF gates</strong>
              <ul className="stack small" style={{ listStyle: "none", padding: 0, margin: "6px 0 0", gap: 4 }}>
                {finalGates.map((g) => (
                  <li key={g.code}>
                    <Chip tone={g.ok ? "ok" : "danger"}>{g.ok ? "✓" : "✗"}</Chip> {g.label}
                    {!g.ok && g.detail ? <span className="muted"> — {g.detail}</span> : null}
                  </li>
                ))}
              </ul>
            </div>
            {lastFailed && !watch ? (
              <Notice tone="error">
                <span data-testid="render-failed">
                  The last render ({String((lastFailed.params_json as { mode?: string }).mode ?? "draft")}, {formatDateTime(lastFailed.created_at)}) failed:{" "}
                  {lastFailed.error_message || lastFailed.error_code || "no reason recorded"}
                </span>
                {lastFailed.correlation_id ? <span className="cid">ref {lastFailed.correlation_id}</span> : null}
              </Notice>
            ) : null}
            {watch ? (
              <RunWatcher
                runId={watch}
                title="Rendering and reconciling the PDF"
                onDone={(r) => {
                  setWatch(null);
                  if (r.status === "succeeded") setSelected(r);
                  void qc.invalidateQueries({ queryKey: qk.runs(eng.id, "render") });
                  void qc.invalidateQueries({ queryKey: qk.gates(eng.id) });
                }}
              />
            ) : null}
          </div>
        </Card>
        <Card title="Reconciliation (PDF-01 … 03)">
          {!current ? (
            <EmptyState>No PDF rendered yet.</EmptyState>
          ) : (
            <div className="stack">
              <div className="row">
                <Chip tone={result.mode === "final" ? "ok" : "accent"}>{result.mode ?? "draft"}</Chip>
                <Chip>{result.engine ?? "?"} {result.engine_version ?? ""}</Chip>
                <Chip>{result.pages ?? "?"} page(s)</Chip>
                <Chip tone={result.misses === 0 ? "ok" : "danger"}>{result.misses === 0 ? "0 misses — PDF matches the workbook" : `${result.misses ?? "?"} miss(es)`}</Chip>
              </div>
              {result.verification?.error_tokens?.length ? <Notice tone="error">Error values printed: {result.verification.error_tokens.join(", ")}</Notice> : null}
              {result.verification?.blank_pages?.length ? <Notice tone="warn">Blank pages: {result.verification.blank_pages.join(", ")}</Notice> : null}
              {result.verification?.reprint_pages?.length ? <Notice tone="warn">Re-print pages: {result.verification.reprint_pages.join(", ")}</Notice> : null}
              {misses.length ? (
                <DataGrid
                  label="Values missing from the PDF"
                  rows={misses.slice(0, 300)}
                  rowKey={(m) => `${m.sheet}!${m.cell}`}
                  maxHeight={260}
                  columns={[
                    { key: "cell", header: "Cell", render: (m) => <span className="mono">{cellRef(m.sheet, m.cell)}</span> },
                    { key: "cap", header: "Caption", className: "wrap", render: (m) => m.caption ?? "" },
                    { key: "p", header: "Printed", className: "num", render: (m) => m.printed },
                    { key: "pg", header: "Pages", render: (m) => (m.pages ?? []).join(", ") },
                  ]}
                />
              ) : null}
            </div>
          )}
        </Card>
      </div>
      {current && pdf ? (
        <Card
          title="Viewer"
          actions={
            current.output_version_id && can("file.download") ? (
              <Button
                size="sm"
                onClick={async () => {
                  try {
                    saveFile(await apiBlob(`/versions/${current.output_version_id}/download`, {}, "statements.pdf"));
                  } catch (e) {
                    toast.error(e);
                  }
                }}
              >
                ⤓ Download (watermarked)
              </Button>
            ) : undefined
          }
        >
          <Suspense fallback={<Spinner label="Loading the viewer" />}>
            <PdfViewer data={pdf} viewer={me?.user.email ?? ""} label={`${eng.name} ${result.mode ?? "draft"} PDF`} />
          </Suspense>
        </Card>
      ) : current?.output_version_id && can("file.download") ? (
        <Spinner label="Loading the PDF" />
      ) : null}
      <Card title="Renders" bodyClass="">
        <DataGrid
          label="Render runs"
          rows={runs.data ?? []}
          rowKey={(r) => r.id}
          onOpen={(r) => r.status === "succeeded" && setSelected(r)}
          selectedKey={current?.id}
          empty="No renders yet."
          columns={[
            { key: "when", header: "When", render: (r) => formatDateTime(r.created_at) },
            { key: "mode", header: "Mode", render: (r) => String((r.params_json as { mode?: string }).mode ?? "draft") },
            { key: "st", header: "Status", render: (r) => <RunStatusChip status={r.status} /> },
            { key: "eng", header: "Engine", render: (r) => String((r.result_json as RenderResult).engine ?? "") },
            { key: "miss", header: "Misses", className: "num", render: (r) => String((r.result_json as RenderResult).misses ?? "") },
          ]}
        />
      </Card>
      {can("release.client") ? (
        <Card title="Release draft to the client (C12)">
          <div className="stack">
            <p className="small muted">The client sees the selected render in the portal; a diff against the previous release is attached automatically.</p>
            <TextArea label="Release note" value={notes} onChange={(e) => setNotes(e.target.value)} style={{ minHeight: 60 }} />
            <div className="row">
              <Button variant="primary" onClick={() => release.mutate()} busy={release.isPending} disabled={!current || result.misses !== 0 || !["REVIEW", "DRAFT_RELEASED_TO_CLIENT"].includes(eng.status)}>
                Release selected PDF
              </Button>
              {!["REVIEW", "DRAFT_RELEASED_TO_CLIENT"].includes(eng.status) ? <span className="small muted">Drafts are released from REVIEW.</span> : null}
            </div>
            <ErrorNotice error={release.error} />
            <DataGrid
              label="Releases"
              rows={releases.data ?? []}
              rowKey={(r) => r.id}
              empty="Nothing released yet."
              columns={[
                { key: "when", header: "Released", render: (r) => formatDateTime(r.released_at) },
                { key: "notes", header: "Note", className: "wrap", render: (r) => r.notes },
                { key: "diff", header: "Changes since previous", render: (r) => ((r.diff_json as { first_release?: boolean }).first_release ? "first release" : `${((r.diff_json as { printed_moves?: unknown[] }).printed_moves ?? []).length} printed figure(s) moved`) },
              ]}
            />
          </div>
        </Card>
      ) : null}
    </div>
  );
}
