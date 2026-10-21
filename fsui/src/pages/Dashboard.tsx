/** Home dashboard (08 §3 screen 2): my engagements, review notes assigned to me, recent activity. */
import { useQueries, useQuery } from "@tanstack/react-query";
import { Link, Navigate } from "react-router-dom";

import { api } from "@/api/client";
import { useEngagements } from "@/api/hooks";
import type { AuditEventOut, Page, ReviewNoteOut } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { isClientUser } from "@/auth/permissions";
import { DataGrid } from "@/components/DataGrid";
import { Card, Chip, EmptyState, EngagementStatusChip, ErrorNotice, PageHeader, Spinner, Stat } from "@/components/ui";
import { formatDate, formatDateTime, titleCase } from "@/lib/format";

export function Dashboard() {
  const { me, can } = useAuth();
  const engagements = useEngagements();
  // firm / system admins see every engagement in lists but open none (no engagement.view): not "my" work
  const list = (engagements.data ?? []).filter((e) => can("engagement.view", e.client_id));
  const notesQueries = useQueries({
    queries: list.slice(0, 12).map((e) => ({
      queryKey: ["engagement", e.id, "review-notes"],
      queryFn: () => api<ReviewNoteOut[]>(`/engagements/${e.id}/review-notes`),
      staleTime: 30_000,
    })),
  });
  const activity = useQuery({
    queryKey: ["audit", "recent"],
    queryFn: () => api<Page>("/audit", { query: { limit: 12 } }),
    enabled: can("audit.view"),
  });

  if (isClientUser(me)) return <Navigate to="/portal" replace />;

  const myNotes = notesQueries
    .flatMap((q, i) => (q.data ?? []).map((n) => ({ n, eng: list[i] })))
    .filter((x) => x.n.assigned_to === me?.user.id && x.n.status !== "Cleared");
  const highs = list.reduce((s, e) => s + (e.open_high_findings ?? 0), 0);
  const today = new Date();
  const overdue = list.filter((e) => e.due_date && new Date(e.due_date) < today && !["FINAL_LOCKED", "ARCHIVED"].includes(e.status)).length;

  return (
    <div className="stack">
      <PageHeader
        title={`Good ${today.getHours() < 12 ? "morning" : today.getHours() < 17 ? "afternoon" : "evening"}, ${me?.user.name.split(" ")[0] ?? ""}`}
        sub="Your engagements, open findings and review notes"
        actions={can("client.manage") ? <Link className="btn primary" to="/clients">New engagement</Link> : undefined}
      />
      <div className="grid-4">
        <Stat label="Engagements" value={list.length} />
        <Stat label="Open High findings" value={highs} tone={highs ? "danger" : "ok"} />
        <Stat label="Review notes for me" value={myNotes.length} tone={myNotes.length ? "warn" : undefined} />
        <Stat label="Past due date" value={overdue} tone={overdue ? "danger" : undefined} />
      </div>
      <Card title="My engagements" bodyClass="">
        {engagements.isLoading ? (
          <div className="card-body">
            <Spinner />
          </div>
        ) : engagements.error ? (
          <div className="card-body">
            <ErrorNotice error={engagements.error} />
          </div>
        ) : (
          <DataGrid
            label="My engagements"
            rows={list}
            rowKey={(e) => e.id}
            empty={<EmptyState>No engagements are assigned to you yet.</EmptyState>}
            columns={[
              { key: "name", header: "Engagement", render: (e) => <Link to={`/engagements/${e.id}`}>{e.name}</Link>, sortValue: (e) => e.name },
              { key: "client", header: "Client / entity", render: (e) => <span>{e.client_name}{e.entity_name && e.entity_name !== e.client_name ? ` · ${e.entity_name}` : ""}</span>, sortValue: (e) => e.client_name ?? "" },
              { key: "type", header: "Type", render: (e) => titleCase(e.type) },
              { key: "period", header: "Period end", render: (e) => formatDate(e.period_end), sortValue: (e) => e.period_end },
              { key: "status", header: "Status", render: (e) => <EngagementStatusChip status={e.status} />, sortValue: (e) => e.status },
              { key: "due", header: "Due", render: (e) => (e.due_date ? formatDate(e.due_date) : "—"), sortValue: (e) => e.due_date ?? "9999" },
              {
                key: "high",
                header: "High findings",
                className: "num",
                render: (e) => (e.open_high_findings === null || e.open_high_findings === undefined ? "—" : e.open_high_findings ? <Chip tone="danger">{e.open_high_findings}</Chip> : <Chip tone="ok">0</Chip>),
                sortValue: (e) => e.open_high_findings ?? -1,
              },
              { key: "notes", header: "Open notes", className: "num", render: (e) => e.open_review_notes ?? "—", sortValue: (e) => e.open_review_notes ?? 0 },
            ]}
          />
        )}
      </Card>
      <div className="grid-2">
        <Card title="Review notes assigned to me">
          {myNotes.length === 0 ? (
            <EmptyState>Nothing waiting for you.</EmptyState>
          ) : (
            <ul className="stack" style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {myNotes.slice(0, 10).map(({ n, eng }) => (
                <li key={n.id} className="row between">
                  <span>
                    <Link to={`/engagements/${eng?.id}/notes`}>{n.text.slice(0, 90)}</Link>
                    <span className="small muted"> · {eng?.name}</span>
                  </span>
                  <Chip tone={n.status === "Open" ? "warn" : "accent"}>{n.status}</Chip>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Recent activity">
          {!can("audit.view") ? (
            <EmptyState>Open an engagement to see its timeline.</EmptyState>
          ) : activity.isLoading ? (
            <Spinner />
          ) : (
            <ul className="stack small" style={{ listStyle: "none", padding: 0, margin: 0, gap: 6 }}>
              {((activity.data?.items ?? []) as AuditEventOut[]).map((ev) => (
                <li key={ev.id} className="row between">
                  <span>
                    <span className="mono">{ev.action}</span>
                    {ev.outcome !== "success" && ev.outcome !== "ok" ? <Chip tone="danger">{ev.outcome}</Chip> : null}
                  </span>
                  <span className="muted">{formatDateTime(ev.ts)}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
