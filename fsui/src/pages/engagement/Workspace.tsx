/** Engagement workspace (08 §3 screen 4): status rail, workflow transitions and tabs. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";

import { api } from "@/api/client";
import { qk, useEngagement } from "@/api/hooks";
import type { EngagementOut } from "@/api/types";
import { ENGAGEMENT_STATES, MANUAL_TRANSITIONS } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { isClientUser } from "@/auth/permissions";
import { Modal } from "@/components/Modal";
import { TabPanel, Tabs } from "@/components/Tabs";
import { useToast } from "@/components/Toast";
import { Button, Chip, EngagementStatusChip, ErrorNotice, Spinner, TextArea } from "@/components/ui";
import { formatDate, titleCase } from "@/lib/format";

import { AiTab } from "./tabs/AiTab";
import { AuditTab } from "./tabs/AuditTab";
import { ChecklistTab } from "./tabs/ChecklistTab";
import { ChecksTab } from "./tabs/ChecksTab";
import { ClientTab } from "./tabs/ClientTab";
import { FilesTab } from "./tabs/FilesTab";
import { FixPlanTab } from "./tabs/FixPlanTab";
import { NarrativeTab } from "./tabs/NarrativeTab";
import { NotesTab } from "./tabs/NotesTab";
import { PdfTab } from "./tabs/PdfTab";
import { RptTab } from "./tabs/RptTab";
import { SettingsTab } from "./tabs/SettingsTab";
import type { TabKey, TabProps } from "./tabKeys";
import { TAB_KEYS } from "./tabKeys";


function StatusRail({ status }: { status: string }) {
  const idx = ENGAGEMENT_STATES.indexOf(status as (typeof ENGAGEMENT_STATES)[number]);
  return (
    <div className="stack" style={{ gap: 4 }}>
      <div className="rail" aria-label={`Workflow step ${idx + 1} of ${ENGAGEMENT_STATES.length}: ${titleCase(status)}`} role="img">
        {ENGAGEMENT_STATES.map((s, i) => (
          <span key={s} className={`step ${i < idx ? "done" : i === idx ? "current" : ""}`} title={titleCase(s)} />
        ))}
      </div>
      <span className="small muted">
        Step {idx + 1} of {ENGAGEMENT_STATES.length}: {titleCase(status)}
      </span>
    </div>
  );
}

function TransitionDialog({ eng, to, label, onClose }: { eng: EngagementOut; to: string; label: string; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [reason, setReason] = useState("");
  const m = useMutation({
    mutationFn: () => api<EngagementOut>(`/engagements/${eng.id}/transition`, { method: "POST", body: { to, reason } }),
    onSuccess: async (e) => {
      qc.setQueryData(qk.engagement(eng.id), e);
      await qc.invalidateQueries({ queryKey: ["engagement", eng.id] });
      await qc.invalidateQueries({ queryKey: ["engagements"] });
      toast.ok(`Moved to ${titleCase(e.status)}`);
      onClose();
    },
  });
  const gates = (m.error as { details?: { gates?: { label: string; detail?: string }[] } } | null)?.details?.gates;
  return (
    <Modal
      title={label}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" busy={m.isPending} onClick={() => m.mutate()}>
            {label}
          </Button>
        </>
      }
    >
      <p>
        Move <strong>{eng.name}</strong> from {titleCase(eng.status)} to <strong>{titleCase(to)}</strong>.
        {to === "FINAL_LOCKED" ? " The final workbook and PDF become write-once (WORM) for the retention period; unlocking needs a Partner and a Firm Admin." : ""}
      </p>
      <TextArea label="Reason / note (audit trail)" value={reason} onChange={(e) => setReason(e.target.value)} />
      <ErrorNotice error={m.error} title="Not moved" />
      {gates?.length ? (
        <ul className="small">
          {gates.map((g) => (
            <li key={g.label}>
              ✗ {g.label}
              {g.detail ? ` — ${g.detail}` : ""}
            </li>
          ))}
        </ul>
      ) : null}
    </Modal>
  );
}

export function Workspace() {
  const { engagementId = "", tab } = useParams();
  const navigate = useNavigate();
  const { me, can } = useAuth();
  const engagement = useEngagement(engagementId);
  const [transition, setTransition] = useState<{ to: string; label: string } | null>(null);

  if (isClientUser(me)) return <Navigate to={`/portal/engagements/${engagementId}`} replace />;
  if (engagement.isLoading) return <Spinner label="Loading engagement" />;
  if (engagement.error || !engagement.data) return <ErrorNotice error={engagement.error ?? new Error("engagement not found")} />;
  const eng = engagement.data;
  const canHere = (p: string) => can(p, eng.client_id);
  const active: TabKey = (TAB_KEYS as readonly string[]).includes(tab ?? "") ? (tab as TabKey) : "checks";
  const transitions = MANUAL_TRANSITIONS.filter(([from, , perm]) => from === eng.status && canHere(perm));
  const props: TabProps = { eng, can: canHere };

  return (
    <div className="stack">
      <div className="page-header">
        <div className="stack" style={{ gap: 6, minWidth: 0 }}>
          <div className="small">
            <Link to="/">Dashboard</Link> / <Link to={`/clients/${eng.client_id}`}>{eng.client_name}</Link>
          </div>
          <h1 style={{ margin: 0 }}>{eng.name}</h1>
          <div className="row small muted">
            <span>{eng.entity_name}</span>·<span>{titleCase(eng.type)}</span>·<span>period end {formatDate(eng.period_end)}</span>
            {eng.due_date ? (
              <>
                ·<span>due {formatDate(eng.due_date)}</span>
              </>
            ) : null}
            <EngagementStatusChip status={eng.status} />
            {eng.is_quarantine ? <Chip tone="warn">Quarantined import</Chip> : null}
            {eng.merge_required ? <Chip tone="danger">Merge required</Chip> : null}
            {eng.legal_hold ? <Chip tone="accent">Legal hold</Chip> : null}
            {eng.version_line > 1 ? <Chip>version line {eng.version_line}</Chip> : null}
            {!eng.ai_enabled ? <Chip>AI off</Chip> : null}
          </div>
          <StatusRail status={eng.status} />
        </div>
        <div className="row">
          {transitions.map(([, to, , label]) => (
            <Button key={to} variant={to === "FINAL_LOCKED" ? "primary" : "default"} onClick={() => setTransition({ to, label })}>
              {label}
            </Button>
          ))}
        </div>
      </div>
      <Tabs
        label="Engagement sections"
        active={active}
        onChange={(k) => navigate(`/engagements/${eng.id}/${k}`)}
        tabs={[
          { key: "files", label: "Files" },
          { key: "checks", label: "Checks", count: eng.open_high_findings ?? null },
          { key: "fix", label: "Fix plan", hidden: !canHere("finding.view") },
          { key: "narrative", label: "Narrative" },
          { key: "rpt", label: "Related parties" },
          { key: "pdf", label: "PDF & release" },
          { key: "notes", label: "Review notes", count: eng.open_review_notes ?? null },
          { key: "client", label: "Client" },
          { key: "checklist", label: "Disclosure checklist" },
          { key: "ai", label: "AI review", hidden: !canHere("ai.request") && !canHere("ai.approve") },
          { key: "audit", label: "Audit timeline" },
          { key: "settings", label: "Settings" },
        ]}
      />
      <TabPanel id={active}>
        {active === "files" ? <FilesTab {...props} /> : null}
        {active === "checks" ? <ChecksTab {...props} /> : null}
        {active === "fix" ? <FixPlanTab {...props} /> : null}
        {active === "narrative" ? <NarrativeTab {...props} /> : null}
        {active === "rpt" ? <RptTab {...props} /> : null}
        {active === "pdf" ? <PdfTab {...props} /> : null}
        {active === "notes" ? <NotesTab {...props} /> : null}
        {active === "client" ? <ClientTab {...props} /> : null}
        {active === "checklist" ? <ChecklistTab {...props} /> : null}
        {active === "ai" ? <AiTab {...props} /> : null}
        {active === "audit" ? <AuditTab {...props} /> : null}
        {active === "settings" ? <SettingsTab {...props} /> : null}
      </TabPanel>
      {transition ? <TransitionDialog eng={eng} to={transition.to} label={transition.label} onClose={() => setTransition(null)} /> : null}
    </div>
  );
}
