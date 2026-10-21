/** Per-engagement audit timeline (06 §4) with filters, CSV export and chain verification. */
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api, apiBlob, saveFile } from "@/api/client";
import { qk } from "@/api/hooks";
import type { AuditEventOut, ChainOut } from "@/api/types";
import { DataGrid } from "@/components/DataGrid";
import { useToast } from "@/components/Toast";
import { Button, Card, Chip, ErrorNotice, Spinner } from "@/components/ui";
import { formatDateTime, shortHash } from "@/lib/format";

import type { TabProps } from "../tabKeys";

export function ChainBadge({ enabled }: { enabled: boolean }) {
  const chain = useQuery({ queryKey: ["audit", "verify"], queryFn: () => api<ChainOut>("/audit/verify"), enabled, staleTime: 60_000, retry: false });
  if (!enabled) return <Chip title="Chain verification is available to the Firm Admin">chain verified nightly</Chip>;
  if (chain.isLoading) return <Spinner label="Verifying chain" />;
  if (chain.error) return <Chip tone="warn">chain check unavailable</Chip>;
  return chain.data?.ok ? (
    <Chip tone="ok" title={`head ${chain.data.head_hash}`}>
      ⛓ chain intact · {chain.data.events} events · head {shortHash(chain.data.head_hash, 10)}
    </Chip>
  ) : (
    <Chip tone="danger">chain broken at #{chain.data?.first_bad_seq}</Chip>
  );
}

export function AuditTab({ eng, can }: TabProps) {
  const toast = useToast();
  const events = useQuery({ queryKey: qk.timeline(eng.id), queryFn: () => api<AuditEventOut[]>(`/engagements/${eng.id}/timeline`, { query: { limit: 1000 } }) });
  const [action, setAction] = useState("");
  const [outcome, setOutcome] = useState("");
  const shown = (events.data ?? []).filter((e) => (!action || e.action.startsWith(action)) && (!outcome || e.outcome === outcome));
  const families = [...new Set((events.data ?? []).map((e) => e.action.split(".")[0] ?? ""))].sort();
  const exportCsv = async () => {
    try {
      saveFile(await apiBlob("/audit/export.csv", { query: { engagement_id: eng.id } }, "audit-trail.csv"));
    } catch (e) {
      toast.error(e, "Export refused");
    }
  };
  return (
    <Card
      title="Audit timeline"
      actions={
        <>
          <ChainBadge enabled={can("users.manage")} />
          {can("audit.view") ? (
            <Button size="sm" onClick={() => void exportCsv()}>
              ⤓ CSV
            </Button>
          ) : null}
        </>
      }
      bodyClass="card-body stack"
    >
      <div className="row">
        <select aria-label="Action" value={action} onChange={(e) => setAction(e.target.value)} style={{ width: "auto" }}>
          <option value="">All actions</option>
          {families.map((f) => (
            <option key={f} value={f}>
              {f}
            </option>
          ))}
        </select>
        <select aria-label="Outcome" value={outcome} onChange={(e) => setOutcome(e.target.value)} style={{ width: "auto" }}>
          <option value="">Any outcome</option>
          <option value="ok">ok</option>
          <option value="denied">denied</option>
        </select>
      </div>
      {events.isLoading ? <Spinner /> : null}
      <ErrorNotice error={events.error} />
      <DataGrid
        label="Audit events"
        rows={shown}
        rowKey={(e) => String(e.id)}
        empty="No events."
        columns={[
          { key: "seq", header: "#", className: "num", render: (e) => e.seq, sortValue: (e) => e.seq },
          { key: "ts", header: "When", render: (e) => formatDateTime(e.ts), sortValue: (e) => e.ts },
          { key: "action", header: "Action", render: (e) => <span className="mono">{e.action}</span>, sortValue: (e) => e.action },
          { key: "who", header: "Role", render: (e) => e.role_at_time ?? (e.user_id ? "user" : "system") },
          { key: "obj", header: "Object", render: (e) => (e.object_type ? `${e.object_type}` : "") },
          { key: "out", header: "Outcome", render: (e) => <Chip tone={e.outcome === "ok" ? undefined : "danger"}>{e.outcome}</Chip> },
          { key: "hash", header: "Event hash", render: (e) => <span className="mono small" title={e.event_hash}>{shortHash(e.event_hash, 10)}</span> },
        ]}
      />
    </Card>
  );
}
