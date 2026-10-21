/** Engagement settings: properties (ETag), configuration YAML with a form for the main fields, export, unlock. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, apiBlob, saveFile } from "@/api/client";
import { qk } from "@/api/hooks";
import type { ConfigOut, EngagementOut, UnlockRequestOut } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { useStepUp } from "@/components/StepUp";
import { useToast } from "@/components/Toast";
import { Button, Card, Checkbox, Chip, ErrorNotice, Notice, SelectInput, TextArea, TextInput } from "@/components/ui";
import { formatDateTime } from "@/lib/format";
import { getYamlValue, setYamlValue } from "@/lib/yaml";

import type { TabProps } from "../tabKeys";

const FORM_FIELDS: { path: string; label: string; options?: string[]; hint?: string }[] = [
  { path: "units", label: "Units", options: ["lakhs", "millions", "crores", "thousands", "rupees"] },
  { path: "period_type", label: "Period type", options: ["annual", "interim", "spfs"] },
  { path: "year_end", label: "Year end (YYYY-MM-DD)" },
  { path: "signing_window_days", label: "Signing window (days)" },
  { path: "policy.balancing_threshold", label: "Balancing threshold (C3)", hint: "Only gaps up to this amount are balanced; larger gaps are investigated." },
  { path: "policy.plug_threshold", label: "Plug threshold" },
  { path: "ai.provider", label: "AI provider", options: ["none", "bedrock", "anthropic", "vertex", "foundry", "gateway", "local", "mock"] },
  { path: "ai.region", label: "AI region", hint: "Required for external providers (e.g. ap-south-1)." },
];

export function SettingsTab({ eng, can }: TabProps) {
  const qc = useQueryClient();
  const toast = useToast();
  const { me } = useAuth();
  const stepUp = useStepUp();
  const config = useQuery({ queryKey: qk.config(eng.id), queryFn: () => api<ConfigOut>(`/engagements/${eng.id}/config`) });
  const unlocks = useQuery({ queryKey: qk.unlocks(eng.id), queryFn: () => api<UnlockRequestOut[]>(`/engagements/${eng.id}/unlock-requests`) });
  const [yaml, setYaml] = useState("");
  const [props, setProps] = useState({ name: eng.name, due_date: eng.due_date ?? "", ai_enabled: eng.ai_enabled, legal_hold: eng.legal_hold });
  const [unlockReason, setUnlockReason] = useState("");

  useEffect(() => {
    if (config.data) setYaml(config.data.yaml_text);
  }, [config.data]);

  const saveConfig = useMutation({
    mutationFn: () => api<ConfigOut>(`/engagements/${eng.id}/config`, { method: "PUT", body: { yaml_text: yaml } }),
    onSuccess: async () => {
      toast.ok("Configuration saved — checks will re-run on the next data change");
      await qc.invalidateQueries({ queryKey: ["engagement", eng.id] });
    },
  });
  const saveProps = useMutation({
    mutationFn: () =>
      api<EngagementOut>(`/engagements/${eng.id}`, {
        method: "PATCH",
        body: { name: props.name, due_date: props.due_date || null, ai_enabled: props.ai_enabled, legal_hold: props.legal_hold },
        ifMatch: `W/"${eng.version}"`,
      }),
    onSuccess: async (e) => {
      qc.setQueryData(qk.engagement(eng.id), e);
      toast.ok("Saved");
    },
  });
  const requestUnlock = useMutation({
    mutationFn: () => api<UnlockRequestOut>(`/engagements/${eng.id}/unlock-requests`, { method: "POST", body: { reason: unlockReason } }),
    onSuccess: async () => {
      setUnlockReason("");
      toast.ok("Unlock requested — a Firm Admin must co-approve");
      await qc.invalidateQueries({ queryKey: qk.unlocks(eng.id) });
    },
    onError: (e) => toast.error(e),
  });
  const coApprove = async (id: string) => {
    try {
      await stepUp.run("Co-approve unlock (dual control)", (reason) => api(`/unlock-requests/${id}/approve`, { method: "POST", reason }));
      toast.ok("Engagement unlocked: a new version line starts");
      await qc.invalidateQueries({ queryKey: ["engagement", eng.id] });
    } catch (e) {
      if ((e as Error).message !== "cancelled") toast.error(e);
    }
  };
  const exportPkg = async () => {
    try {
      saveFile(await apiBlob(`/engagements/${eng.id}/export`, { method: "POST" }, `${eng.name}.fspkg`));
      toast.info("Encrypted, signed engagement package downloaded");
    } catch (e) {
      toast.error(e, "Export refused");
    }
  };
  const locked = eng.status === "FINAL_LOCKED" || eng.status === "ARCHIVED";

  return (
    <div className="stack">
      <div className="grid-2">
        <Card title="Engagement">
          <div className="stack">
            <TextInput label="Name" value={props.name} onChange={(e) => setProps((p) => ({ ...p, name: e.target.value }))} disabled={!can("engagement.assign")} />
            <TextInput label="Due date" type="date" value={props.due_date} onChange={(e) => setProps((p) => ({ ...p, due_date: e.target.value }))} disabled={!can("engagement.assign")} />
            <Checkbox label="AI review allowed for this engagement (kill switch)" checked={props.ai_enabled} onChange={(e) => setProps((p) => ({ ...p, ai_enabled: e.target.checked }))} disabled={!can("engagement.assign")} />
            <Checkbox label="Legal hold (retention)" checked={props.legal_hold} onChange={(e) => setProps((p) => ({ ...p, legal_hold: e.target.checked }))} disabled={!can("engagement.lock") && !can("admin.compliance")} />
            <ErrorNotice error={saveProps.error} />
            <div className="row">
              <Button variant="primary" onClick={() => saveProps.mutate()} busy={saveProps.isPending} disabled={!can("engagement.assign")}>
                Save
              </Button>
              {can("engagement.export") ? <Button onClick={() => void exportPkg()}>Export package (.fspkg)</Button> : null}
            </div>
          </div>
        </Card>
        <Card title="Lock and dual-control unlock">
          <div className="stack">
            {locked ? <Notice tone="ok">Locked {eng.locked_at ? formatDateTime(eng.locked_at) : ""}: files are write-once (WORM).</Notice> : <p className="small muted">Unlocking a FINAL_LOCKED engagement needs a Partner's request and a Firm Admin's co-approval, and starts a new version line.</p>}
            {locked && can("engagement.unlock") ? (
              <>
                <TextArea label="Reason for unlocking" value={unlockReason} onChange={(e) => setUnlockReason(e.target.value)} style={{ minHeight: 60 }} />
                <Button onClick={() => requestUnlock.mutate()} busy={requestUnlock.isPending} disabled={unlockReason.trim().length < 10}>
                  Request unlock
                </Button>
              </>
            ) : null}
            {(unlocks.data ?? []).map((u) => (
              <div key={u.id} className="row between sentence">
                <span className="small">
                  {formatDateTime(u.created_at)} — {u.reason}
                </span>
                <span className="row">
                  <Chip tone={u.status === "completed" ? "ok" : "warn"}>{u.status}</Chip>
                  {u.status === "pending" && can("engagement.unlock_coapprove") && u.requested_by !== me?.user.id ? (
                    <Button size="sm" variant="primary" onClick={() => void coApprove(u.id)}>
                      Co-approve
                    </Button>
                  ) : null}
                </span>
              </div>
            ))}
          </div>
        </Card>
      </div>
      <Card
        title={`Configuration${config.data ? ` (version ${config.data.version})` : ""}`}
        actions={
          can("config.edit") ? (
            <Button variant="primary" onClick={() => saveConfig.mutate()} busy={saveConfig.isPending} disabled={!yaml || yaml === config.data?.yaml_text}>
              Save configuration
            </Button>
          ) : undefined
        }
      >
        <div className="stack">
          <div className="grid-4">
            {FORM_FIELDS.map((f) => {
              const v = getYamlValue(yaml, f.path) ?? "";
              const set = (nv: string) => setYaml((y) => setYamlValue(y, f.path, nv));
              return f.options ? (
                <SelectInput key={f.path} label={f.label} hint={f.hint} value={v} onChange={(e) => set(e.target.value)} options={f.options.includes(v) || !v ? f.options : [v, ...f.options]} disabled={!can("config.edit")} />
              ) : (
                <TextInput key={f.path} label={f.label} hint={f.hint} value={v} onChange={(e) => set(e.target.value)} disabled={!can("config.edit")} />
              );
            })}
          </div>
          <TextArea label="YAML (02 §8)" className="code" value={yaml} onChange={(e) => setYaml(e.target.value)} disabled={!can("config.edit")} spellCheck={false} />
          <ErrorNotice error={saveConfig.error} title="Configuration not saved" />
        </div>
      </Card>
    </div>
  );
}
