/**
 * AI review (02 §9, C9, E-AI-01…05): payload preview, approval by a different
 * person bound to the payload hash, send through the AI gateway, proposals only.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "@/api/client";
import { qk } from "@/api/hooks";
import type { AIPolicyOut, AIRequestOut, RunOut } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { DataGrid } from "@/components/DataGrid";
import { RunWatcher } from "@/components/RunWatcher";
import { useToast } from "@/components/Toast";
import { Button, Card, Chip, EmptyState, ErrorNotice, KeyValue, Notice, SelectInput, Spinner, TextInput } from "@/components/ui";
import { formatBytes, formatDateTime, shortHash, titleCase } from "@/lib/format";

import type { TabProps } from "../tabKeys";

export function AiTab({ eng, can }: TabProps) {
  const qc = useQueryClient();
  const toast = useToast();
  const { me } = useAuth();
  const policy = useQuery({ queryKey: ["ai-policy"], queryFn: () => api<AIPolicyOut>("/ai/policy") });
  const requests = useQuery({ queryKey: qk.ai(eng.id), queryFn: () => api<AIRequestOut[]>(`/engagements/${eng.id}/ai/requests`) });
  const [kind, setKind] = useState("triage");
  const [reason, setReason] = useState("");
  const [provider, setProvider] = useState("");
  const [region, setRegion] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);

  const accept = useMutation({
    mutationFn: () => api<AIPolicyOut>("/ai/policy/accept", { method: "POST" }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["ai-policy"] });
      await qc.invalidateQueries({ queryKey: qk.me });
    },
    onError: (e) => toast.error(e),
  });
  const create = useMutation({
    mutationFn: () =>
      api<AIRequestOut>(`/engagements/${eng.id}/ai/requests`, {
        method: "POST",
        body: { kind, reason, provider: provider || null, region: region || null },
      }),
    onSuccess: async (r) => {
      setOpenId(r.id);
      await qc.invalidateQueries({ queryKey: qk.ai(eng.id) });
    },
    onError: (e) => toast.error(e, "Preview not created"),
  });

  if (!eng.ai_enabled) return <Notice>AI review is switched off for this engagement (per-engagement kill switch). A Manager can enable it in Settings.</Notice>;
  return (
    <div className="stack">
      <Notice>
        AI output is <strong>advisory</strong>: it never edits a workbook, every number it cites is re-verified by the engine, and every payload is
        pseudonymised, shown in full, and approved by a different person before it leaves the firm (control C9).
      </Notice>
      {policy.isLoading ? <Spinner /> : null}
      {policy.data && !policy.data.accepted ? (
        <Card title={`AI use policy (version ${policy.data.version})`} actions={<Button variant="primary" onClick={() => accept.mutate()} busy={accept.isPending}>I accept</Button>}>
          <pre className="small">{policy.data.body}</pre>
        </Card>
      ) : null}
      {policy.error ? <ErrorNotice error={policy.error} title="AI policy" /> : null}
      {can("ai.request") && policy.data?.accepted ? (
        <Card title="Request an AI review">
          <div className="grid-4">
            <SelectInput
              label="Purpose"
              value={kind}
              onChange={(e) => setKind(e.target.value)}
              options={[
                { value: "triage", label: "Triage findings" },
                { value: "narrative", label: "Narrative / Going Concern review" },
                { value: "xref", label: "Cross-reference suggestions" },
                { value: "explain", label: "Explain mismatches" },
              ]}
            />
            <TextInput label="Reason" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why is AI needed here?" />
            <SelectInput
              label="Provider (default from settings)"
              value={provider}
              onChange={(e) => setProvider(e.target.value)}
              options={[{ value: "", label: "Engagement default" }, "bedrock", "anthropic", "vertex", "foundry", "gateway", "local", "mock"]}
            />
            <TextInput label="Region" value={region} onChange={(e) => setRegion(e.target.value)} placeholder="e.g. ap-south-1" hint="Required for external providers; no default by design." />
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <Button variant="primary" onClick={() => create.mutate()} busy={create.isPending} disabled={reason.trim().length < 3}>
              Build payload preview
            </Button>
          </div>
        </Card>
      ) : null}
      {openId ? <RequestDetail id={openId} meId={me?.user.id} can={can} engagementId={eng.id} onClose={() => setOpenId(null)} /> : null}
      <Card title="AI requests" bodyClass="">
        {requests.error ? <ErrorNotice error={requests.error} /> : null}
        <DataGrid
          label="AI requests"
          rows={requests.data ?? []}
          rowKey={(r) => r.id}
          onOpen={(r) => setOpenId(r.id)}
          selectedKey={openId}
          empty={<EmptyState>No AI requests yet.</EmptyState>}
          columns={[
            { key: "when", header: "Created", render: (r) => formatDateTime(r.created_at) },
            { key: "kind", header: "Purpose", render: (r) => titleCase(r.kind) },
            { key: "st", header: "Status", render: (r) => <Chip tone={r.status === "completed" ? "ok" : r.status === "rejected" || r.status === "refused" ? "danger" : "warn"}>{r.status}</Chip> },
            { key: "dest", header: "Destination", render: (r) => `${r.provider} · ${r.model}${r.region ? ` · ${r.region}` : ""}` },
            { key: "hash", header: "Payload", render: (r) => <span className="mono">{shortHash(r.payload_sha256)}</span> },
            { key: "cost", header: "Cost (USD)", className: "num", render: (r) => r.cost_usd.toFixed(4) },
          ]}
        />
      </Card>
    </div>
  );
}

function RequestDetail({ id, meId, can, engagementId, onClose }: { id: string; meId?: string; can: (p: string) => boolean; engagementId: string; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const req = useQuery({ queryKey: ["ai-request", id], queryFn: () => api<AIRequestOut>(`/ai/requests/${id}`) });
  const [sendRun, setSendRun] = useState<string | null>(null);
  const refresh = async () => {
    await qc.invalidateQueries({ queryKey: ["ai-request", id] });
    await qc.invalidateQueries({ queryKey: qk.ai(engagementId) });
  };
  const approve = useMutation({
    mutationFn: (sha: string) => api<AIRequestOut>(`/ai/requests/${id}/approve`, { method: "POST", body: { payload_sha256: sha } }),
    onSuccess: async () => {
      toast.ok("Payload approved — bound to its SHA-256");
      await refresh();
    },
    onError: (e) => toast.error(e, "Not approved"),
  });
  const send = useMutation({
    mutationFn: () => api<RunOut>(`/ai/requests/${id}/send`, { method: "POST" }),
    onSuccess: (r) => setSendRun(r.id),
    onError: (e) => toast.error(e, "Not sent"),
  });
  if (req.isLoading) return <Spinner />;
  if (req.error || !req.data) return <ErrorNotice error={req.error} />;
  const r = req.data;
  const own = r.requested_by === meId;
  return (
    <Card title={`AI request — ${titleCase(r.kind)}`} actions={<Button variant="ghost" onClick={onClose}>Close</Button>}>
      <div className="stack">
        <KeyValue
          items={[
            ["Status", r.status],
            ["Destination", `${r.provider} · ${r.model} · region ${r.region ?? "—"}`],
            ["Prompt version", r.prompt_version],
            ["Payload", `${formatBytes(r.payload_size)} · ~${r.token_estimate} tokens · SHA-256 ${shortHash(r.payload_sha256, 24)}`],
            ["Reason", r.reason],
            ["Stop reason", r.stop_reason],
            ["Usage", r.input_tokens || r.output_tokens ? `${r.input_tokens} in / ${r.output_tokens} out` : null],
          ]}
        />
        {r.status === "preview" ? (
          own ? (
            <Notice tone="warn">You requested this review: a different person must approve the exact payload below.</Notice>
          ) : can("ai.approve") ? (
            <Button variant="primary" onClick={() => approve.mutate(r.payload_sha256)} busy={approve.isPending}>
              Approve this exact payload
            </Button>
          ) : null
        ) : null}
        {r.status === "approved" && (own || can("ai.approve")) ? (
          <Button variant="primary" onClick={() => send.mutate()} busy={send.isPending}>
            Send through the AI gateway
          </Button>
        ) : null}
        {sendRun ? <RunWatcher runId={sendRun} title="Sending through the AI gateway" onDone={() => void refresh()} /> : null}
        <details open={r.status === "preview"}>
          <summary>Exact payload (pseudonymised) — this is everything that would leave the firm</summary>
          <pre className="small" style={{ maxHeight: 360, overflow: "auto" }} data-testid="ai-payload">
            {JSON.stringify(r.payload ?? {}, null, 2)}
          </pre>
        </details>
        {r.response ? (
          <details open>
            <summary>Response — proposals only</summary>
            <pre className="small" style={{ maxHeight: 360, overflow: "auto" }}>
              {JSON.stringify(r.response, null, 2)}
            </pre>
          </details>
        ) : null}
        {r.error ? <Notice tone="error">{r.error}</Notice> : null}
      </div>
    </Card>
  );
}
