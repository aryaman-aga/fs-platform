/** Client portal (08 §3 screen 5): released drafts, queries, uploads, management representations. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api, apiBlob, saveFile } from "@/api/client";
import { ENGAGEMENT_STATES } from "@/api/types";
import type { EngagementOut, FindingOut, MgmtItemOut, ReleaseOut, ReviewNoteOut, UploadOut } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { DataGrid } from "@/components/DataGrid";
import { useToast } from "@/components/Toast";
import { Button, Card, Chip, EmptyState, EngagementStatusChip, ErrorNotice, Notice, PageHeader, SelectInput, SeverityBadge, Spinner, TextArea } from "@/components/ui";
import { cellsLabel } from "@/lib/cells";
import { formatDate, formatDateTime } from "@/lib/format";

export function PortalHome() {
  const { me } = useAuth();
  const engagements = useQuery({ queryKey: ["portal", "engagements"], queryFn: () => api<EngagementOut[]>("/portal/engagements") });
  return (
    <div className="stack">
      <PageHeader title="Client portal" sub={`Welcome, ${me?.user.name ?? ""}. Drafts released by your audit team, their queries and the items they need you to confirm.`} />
      {engagements.isLoading ? <Spinner /> : null}
      <ErrorNotice error={engagements.error} />
      <Card bodyClass="">
        <DataGrid
          label="Your engagements"
          rows={engagements.data ?? []}
          rowKey={(e) => e.id}
          empty={<EmptyState>No engagements are shared with you yet.</EmptyState>}
          columns={[
            { key: "n", header: "Engagement", render: (e) => <Link to={`/portal/engagements/${e.id}`}>{e.name}</Link> },
            { key: "p", header: "Period end", render: (e) => formatDate(e.period_end) },
            { key: "s", header: "Status", render: (e) => <EngagementStatusChip status={e.status} /> },
          ]}
        />
      </Card>
    </div>
  );
}

export function PortalEngagement() {
  const { engagementId = "" } = useParams();
  const qc = useQueryClient();
  const toast = useToast();
  const { can } = useAuth();
  const base = `/portal/engagements/${engagementId}`;
  const eng = useQuery({ queryKey: ["portal", "engagements"], queryFn: () => api<EngagementOut[]>("/portal/engagements"), select: (xs) => xs.find((x) => x.id === engagementId) });
  const releases = useQuery({ queryKey: ["portal", engagementId, "releases"], queryFn: () => api<ReleaseOut[]>(`${base}/releases`) });
  const queries = useQuery({ queryKey: ["portal", engagementId, "queries"], queryFn: () => api<ReviewNoteOut[]>(`${base}/queries`) });
  const mgmt = useQuery({ queryKey: ["portal", engagementId, "mgmt"], queryFn: () => api<MgmtItemOut[]>(`${base}/mgmt-items`) });
  // shared findings are readable once a draft has been released to the client (ABAC client-sees-released-only)
  const status = eng.data?.status as (typeof ENGAGEMENT_STATES)[number] | undefined;
  const released = !!status && ENGAGEMENT_STATES.indexOf(status) >= ENGAGEMENT_STATES.indexOf("DRAFT_RELEASED_TO_CLIENT");
  const findings = useQuery({ queryKey: ["portal", engagementId, "findings"], queryFn: () => api<FindingOut[]>(`${base}/findings`), enabled: can("finding.view") && released });
  const [kind, setKind] = useState("tb");
  const upload = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      form.append("kind", kind);
      return api<UploadOut>(`/engagements/${engagementId}/files`, { method: "POST", form });
    },
    onSuccess: (o) => toast.ok(`Uploaded ${o.version.original_name}; your audit team has been notified.`),
    onError: (e) => toast.error(e, "Upload refused"),
  });
  const download = async (r: ReleaseOut) => {
    try {
      saveFile(await apiBlob(`/portal/releases/${r.id}/download`, {}, "draft.pdf"));
      toast.info("Downloaded a copy watermarked with your name and the time.");
    } catch (e) {
      toast.error(e, "Download refused");
    }
  };
  const e = eng.data;
  return (
    <div className="stack">
      <PageHeader title={e?.name ?? "Engagement"} sub={<Link to="/portal">← All engagements</Link>} actions={e ? <EngagementStatusChip status={e.status} /> : undefined} />
      <div className="grid-2">
        <Card title="Released drafts">
          {releases.isLoading ? <Spinner /> : null}
          <ErrorNotice error={releases.error} />
          {(releases.data ?? []).length === 0 && !releases.isLoading ? <EmptyState>No draft has been released to you yet.</EmptyState> : null}
          <ul className="stack" style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {(releases.data ?? []).map((r) => (
              <li key={r.id} className="row between sentence">
                <span>
                  Draft released {formatDateTime(r.released_at)}
                  {r.notes ? <span className="small muted"> — {r.notes}</span> : null}
                </span>
                {can("file.download") ? (
                  <Button size="sm" onClick={() => void download(r)}>
                    ⤓ PDF
                  </Button>
                ) : (
                  <Chip>view only</Chip>
                )}
              </li>
            ))}
          </ul>
        </Card>
        {can("file.upload") ? (
          <Card title="Upload requested files">
            <div className="stack">
              <SelectInput label="What are you uploading?" value={kind} onChange={(ev) => setKind(ev.target.value)} options={[{ value: "tb", label: "Trial balance" }, { value: "workbook", label: "Financial statements workbook" }, { value: "other", label: "Supporting document" }]} />
              <input type="file" accept=".xlsx,.xlsm,.pdf,.csv" aria-label="Choose a file to upload" onChange={(ev) => { const f = ev.target.files?.[0]; if (f) upload.mutate(f); ev.target.value = ""; }} />
              {upload.isPending ? <Spinner label="Uploading" /> : null}
              <p className="small muted">Accepted: .xlsx, .xlsm, .pdf, .csv. Files are scanned and stored as immutable versions.</p>
            </div>
          </Card>
        ) : null}
      </div>
      <Card title="Queries from your audit team">
        {(queries.data ?? []).length === 0 ? <EmptyState>No open queries.</EmptyState> : null}
        <div className="stack">
          {(queries.data ?? []).map((q) => (
            <PortalQuery key={q.id} q={q} engagementId={engagementId} canRespond={can("portal.respond")} />
          ))}
        </div>
      </Card>
      <Card title="Management representations">
        {(mgmt.data ?? []).length === 0 ? <EmptyState>Nothing to confirm.</EmptyState> : null}
        <div className="stack">
          {(mgmt.data ?? []).map((m) => (
            <MgmtItem key={m.id} m={m} canConfirm={can("portal.confirm") && e?.status === "DRAFT_RELEASED_TO_CLIENT"} onDone={() => void qc.invalidateQueries({ queryKey: ["portal"] })} />
          ))}
        </div>
        {can("portal.confirm") && e && e.status !== "DRAFT_RELEASED_TO_CLIENT" ? <Notice>Representations are confirmed after a draft is released to you.</Notice> : null}
      </Card>
      {can("finding.view") && (findings.data ?? []).length ? (
        <Card title="Points shared with you" bodyClass="">
          <DataGrid
            label="Shared findings"
            rows={findings.data ?? []}
            rowKey={(f) => f.id}
            columns={[
              { key: "s", header: "Severity", render: (f) => <SeverityBadge severity={f.severity} /> },
              { key: "w", header: "Where", render: (f) => <span className="mono small">{cellsLabel(f.sheet, f.cells)}</span> },
              { key: "e", header: "Point", className: "wrap", render: (f) => f.explanation },
            ]}
          />
        </Card>
      ) : null}
    </div>
  );
}

function PortalQuery({ q, engagementId, canRespond }: { q: ReviewNoteOut; engagementId: string; canRespond: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [text, setText] = useState("");
  const respond = useMutation({
    mutationFn: () => api(`/portal/queries/${q.id}/respond`, { method: "POST", body: { text } }),
    onSuccess: async () => {
      setText("");
      toast.ok("Response sent");
      await qc.invalidateQueries({ queryKey: ["portal", engagementId, "queries"] });
    },
    onError: (e) => toast.error(e),
  });
  return (
    <div className="sentence stack" style={{ gap: 6 }}>
      <div className="row between">
        <Chip tone={q.status === "Cleared" ? "ok" : q.status === "Responded" ? "accent" : "warn"}>{q.status}</Chip>
        <span className="small muted">{formatDateTime(q.created_at)}</span>
      </div>
      <p style={{ margin: 0 }}>{q.text}</p>
      {(q.messages ?? []).map((m) => (
        <p key={m.id} className="small" style={{ margin: "0 0 0 12px" }}>
          ↳ {m.text}
        </p>
      ))}
      {canRespond && q.status !== "Cleared" ? (
        <div className="row" style={{ alignItems: "flex-end" }}>
          <div style={{ flex: 1 }}>
            <TextArea label="Your response" value={text} onChange={(e) => setText(e.target.value)} style={{ minHeight: 50 }} />
          </div>
          <Button onClick={() => respond.mutate()} busy={respond.isPending} disabled={!text.trim()}>
            Send
          </Button>
        </div>
      ) : null}
    </div>
  );
}

function MgmtItem({ m, canConfirm, onDone }: { m: MgmtItemOut; canConfirm: boolean; onDone: () => void }) {
  const toast = useToast();
  const [comment, setComment] = useState(m.comment ?? "");
  const decide = useMutation({
    mutationFn: (decision: "confirmed" | "disputed") => api(`/portal/mgmt-items/${m.id}/confirm`, { method: "POST", body: { decision, comment: comment || null } }),
    onSuccess: () => {
      toast.ok("Recorded");
      onDone();
    },
    onError: (e) => toast.error(e),
  });
  return (
    <div className="sentence stack" style={{ gap: 6 }}>
      <div className="row between">
        <span>{m.text}</span>
        <Chip tone={m.status === "confirmed" ? "ok" : m.status === "disputed" ? "danger" : "warn"}>{m.status}</Chip>
      </div>
      {canConfirm && m.status !== "confirmed" ? (
        <div className="row" style={{ alignItems: "flex-end" }}>
          <div style={{ flex: 1 }}>
            <TextArea label="Comment (optional)" value={comment} onChange={(e) => setComment(e.target.value)} style={{ minHeight: 40 }} />
          </div>
          <Button variant="primary" onClick={() => decide.mutate("confirmed")} busy={decide.isPending}>
            Confirm
          </Button>
          <Button onClick={() => decide.mutate("disputed")}>Dispute</Button>
        </div>
      ) : null}
    </div>
  );
}
