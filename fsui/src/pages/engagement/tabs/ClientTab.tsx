/** Client-facing items (firm side): management-representation items and queries to the client. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "@/api/client";
import { qk, useReviewNotes } from "@/api/hooks";
import type { MgmtItemOut } from "@/api/types";
import { DataGrid } from "@/components/DataGrid";
import { useToast } from "@/components/Toast";
import { Button, Card, Chip, ErrorNotice, TextArea } from "@/components/ui";
import { formatDateTime } from "@/lib/format";

import type { TabProps } from "../tabKeys";

export function ClientTab({ eng, can }: TabProps) {
  const qc = useQueryClient();
  const toast = useToast();
  const mgmt = useQuery({ queryKey: qk.mgmt(eng.id), queryFn: () => api<MgmtItemOut[]>(`/engagements/${eng.id}/mgmt-items`) });
  const notes = useReviewNotes(eng.id);
  const [item, setItem] = useState("");
  const [query, setQuery] = useState("");
  const add = useMutation({
    mutationFn: () => api<MgmtItemOut>(`/engagements/${eng.id}/mgmt-items`, { method: "POST", body: { text: item } }),
    onSuccess: async () => {
      setItem("");
      await qc.invalidateQueries({ queryKey: qk.mgmt(eng.id) });
    },
    onError: (e) => toast.error(e),
  });
  const ask = useMutation({
    mutationFn: () => api(`/engagements/${eng.id}/review-notes`, { method: "POST", body: { target_type: "client_query", text: query } }),
    onSuccess: async () => {
      setQuery("");
      toast.ok("Query sent to the client portal");
      await qc.invalidateQueries({ queryKey: qk.notes(eng.id) });
    },
    onError: (e) => toast.error(e),
  });
  const queries = (notes.data ?? []).filter((n) => n.audience === "client");
  const pending = (mgmt.data ?? []).filter((m) => m.status !== "confirmed").length;

  return (
    <div className="grid-2">
      <Card title={`Management representations (${pending} pending)`}>
        <div className="stack">
          <DataGrid
            label="Management representation items"
            rows={mgmt.data ?? []}
            rowKey={(m) => m.id}
            empty="No representation items yet."
            columns={[
              { key: "t", header: "Item", className: "wrap", render: (m) => m.text },
              { key: "s", header: "Status", render: (m) => <Chip tone={m.status === "confirmed" ? "ok" : m.status === "disputed" ? "danger" : "warn"}>{m.status}</Chip> },
              { key: "w", header: "Decided", render: (m) => (m.confirmed_at ? formatDateTime(m.confirmed_at) : "—") },
              { key: "c", header: "Client comment", className: "wrap", render: (m) => m.comment ?? "" },
            ]}
          />
          {can("release.client") ? (
            <>
              <TextArea label="New item for the client to confirm" value={item} onChange={(e) => setItem(e.target.value)} style={{ minHeight: 60 }} />
              <Button onClick={() => add.mutate()} busy={add.isPending} disabled={item.trim().length < 3}>
                Add item
              </Button>
            </>
          ) : null}
          <ErrorNotice error={mgmt.error} />
        </div>
      </Card>
      <Card title={`Queries to the client (${queries.length})`}>
        <div className="stack">
          {queries.map((q) => (
            <div key={q.id} className="sentence">
              <div className="row between small">
                <Chip tone={q.status === "Responded" ? "accent" : q.status === "Cleared" ? "ok" : "warn"}>{q.status}</Chip>
                <span className="muted">{formatDateTime(q.created_at)}</span>
              </div>
              <p style={{ margin: "6px 0" }}>{q.text}</p>
              {(q.messages ?? []).map((m) => (
                <p key={m.id} className="small" style={{ margin: "2px 0 0 12px" }}>
                  ↳ {m.text}
                </p>
              ))}
            </div>
          ))}
          {can("release.client") ? (
            <>
              <TextArea label="Ask the client" value={query} onChange={(e) => setQuery(e.target.value)} style={{ minHeight: 60 }} />
              <Button onClick={() => ask.mutate()} busy={ask.isPending} disabled={!query.trim()}>
                Send query
              </Button>
            </>
          ) : null}
        </div>
      </Card>
    </div>
  );
}
