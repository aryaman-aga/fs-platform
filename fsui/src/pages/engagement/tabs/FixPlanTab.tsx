/** Fix plan with maker-checker (C2), balancing within rounding (C3), apply + verification (C5). */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { api } from "@/api/client";
import { latestSucceeded, qk, useFixPlans, useRuns } from "@/api/hooks";
import type { BalancingAdjustment, EditOut, FixPlanOut, PrintedMove, RunOut, VerificationReport } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { Amount } from "@/components/Prefs";
import { DataGrid } from "@/components/DataGrid";
import { Modal } from "@/components/Modal";
import { RunWatcher } from "@/components/RunWatcher";
import { useToast } from "@/components/Toast";
import { Button, Card, Checkbox, Chip, EmptyState, ErrorNotice, KeyValue, Notice, Spinner, TextArea } from "@/components/ui";
import { cellRef } from "@/lib/cells";
import { formatDateTime, shortHash, titleCase } from "@/lib/format";

import type { TabProps } from "../tabKeys";

const GROUP_ORDER = ["totals_rewrite", "plug_removal", "missing_total", "balancing", "py_line_adjust", "link", "typed_text", "hide", "other"];
const GROUP_LABEL: Record<string, string> = {
  totals_rewrite: "Totals rewritten to add printed components",
  plug_removal: "Typed rounding plugs removed",
  missing_total: "Missing total cells added",
  balancing: "Balancing lines (rounding only)",
  py_line_adjust: "Previous-year line adjustments",
  link: "Links to source",
  typed_text: "Typed-text corrections",
  hide: "Hide rows / columns",
  other: "Other",
};

export function FixPlanTab({ eng, can }: TabProps) {
  const qc = useQueryClient();
  const toast = useToast();
  const plans = useFixPlans(eng.id);
  const runs = useRuns(eng.id, "check");
  const latestCheck = latestSucceeded(runs.data, "check");
  const current = (plans.data ?? []).find((p) => !["superseded"].includes(p.status));
  const [planRun, setPlanRun] = useState<string | null>(null);
  const watching = planRun ?? (current?.status === "planning" ? (current.plan_run_id ?? null) : null);

  const propose = useMutation({
    mutationFn: () => api<FixPlanOut>(`/runs/${latestCheck?.id}/fix-plan`, { method: "POST" }),
    onSuccess: async (p) => {
      setPlanRun(p.plan_run_id ?? null);
      await qc.invalidateQueries({ queryKey: qk.plans(eng.id) });
    },
    onError: (e) => toast.error(e, "Plan not proposed"),
  });

  return (
    <div className="stack">
      <div className="row between">
        <p className="muted" style={{ margin: 0 }}>
          Fixes are written to a <strong>new version</strong>; the original is never modified (C1). The proposer cannot approve (C2).
        </p>
        {can("fix.propose") ? (
          <Button variant="primary" onClick={() => propose.mutate()} busy={propose.isPending} disabled={!latestCheck} data-testid="propose-plan">
            Propose fix plan
          </Button>
        ) : null}
      </div>
      {!latestCheck ? <Notice>Run checks first: plans are proposed from the latest successful check.</Notice> : null}
      {watching ? (
        <RunWatcher
          runId={watching}
          title="Planning fixes (fixed-point totals rewrite, balancing solve)"
          onDone={() => {
            setPlanRun(null);
            void qc.invalidateQueries({ queryKey: qk.plans(eng.id) });
          }}
        />
      ) : null}
      {plans.isLoading ? <Spinner /> : null}
      <ErrorNotice error={plans.error} />
      {current ? <PlanView key={current.id} eng={eng} can={can} plan={current} /> : !plans.isLoading ? <EmptyState>No fix plan yet.</EmptyState> : null}
      {(plans.data ?? []).length > 1 ? (
        <Card title="Earlier plans" bodyClass="">
          <DataGrid
            label="Earlier fix plans"
            rows={(plans.data ?? []).filter((p) => p.id !== current?.id)}
            rowKey={(p) => p.id}
            columns={[
              { key: "when", header: "Created", render: (p) => formatDateTime(p.created_at) },
              { key: "status", header: "Status", render: (p) => <Chip>{p.status}</Chip> },
              { key: "hash", header: "Plan hash", render: (p) => <span className="mono">{shortHash(p.plan_sha256)}</span> },
              { key: "edits", header: "Edits", className: "num", render: (p) => (p.edits ?? []).length },
              { key: "why", header: "Note", render: (p) => p.rejection_reason ?? "" },
            ]}
          />
        </Card>
      ) : null}
    </div>
  );
}

function PlanView({ eng, can, plan }: TabProps & { plan: FixPlanOut }) {
  const qc = useQueryClient();
  const toast = useToast();
  const { me } = useAuth();
  const edits = useMemo(() => plan.edits ?? [], [plan.edits]);
  const approvalItems = edits.filter((e) => e.fix_kind === "approval");
  const [chosen, setChosen] = useState<string[]>(plan.approved_edit_ids?.map(String) ?? []);
  const [rejecting, setRejecting] = useState(false);
  const [applyRun, setApplyRun] = useState<string | null>(plan.apply_run_id && plan.status === "applying" ? plan.apply_run_id : null);
  const isProposer = plan.proposed_by === me?.user.id;
  const groups = useMemo(() => {
    const m = new Map<string, EditOut[]>();
    for (const e of edits) m.set(e.grp, [...(m.get(e.grp) ?? []), e]);
    return GROUP_ORDER.filter((g) => m.has(g)).map((g) => [g, m.get(g) ?? []] as const);
  }, [edits]);
  const moves = (plan.printed_moves ?? []) as PrintedMove[];
  const balancing = (plan.balancing ?? []) as BalancingAdjustment[];
  const refresh = () => Promise.all([qc.invalidateQueries({ queryKey: qk.plans(eng.id) }), qc.invalidateQueries({ queryKey: qk.engagement(eng.id) })]);

  const approve = useMutation({
    mutationFn: () => api<FixPlanOut>(`/fix-plans/${plan.id}/approve`, { method: "POST", body: { plan_sha256: plan.plan_sha256, approved_edit_ids: chosen } }),
    onSuccess: async (p) => {
      toast.ok(p.status === "pending_balancing" ? "Approved — balancing lines now need a Partner's check" : "Plan approved");
      await refresh();
    },
    onError: (e) => toast.error(e, "Not approved"),
  });
  const approveBalancing = useMutation({
    mutationFn: () => api<FixPlanOut>(`/fix-plans/${plan.id}/approve-balancing`, { method: "POST", body: { plan_sha256: plan.approved_plan_sha256 ?? plan.plan_sha256 } }),
    onSuccess: async () => {
      toast.ok("Balancing adjustment approved");
      await refresh();
    },
    onError: (e) => toast.error(e, "Not approved"),
  });
  const apply = useMutation({
    mutationFn: () => api<RunOut>(`/fix-plans/${plan.id}/apply`, { method: "POST" }),
    onSuccess: (r) => setApplyRun(r.id),
    onError: (e) => toast.error(e, "Not applied"),
  });

  const statusTone = plan.status === "applied" ? "ok" : plan.status === "failed" || plan.status === "rejected" ? "danger" : plan.status === "approved" ? "accent" : "warn";

  return (
    <div className="stack">
      <Card
        title={
          <div className="row">
            <h2 style={{ margin: 0 }}>Fix plan</h2>
            <Chip tone={statusTone}>{titleCase(plan.status)}</Chip>
          </div>
        }
        actions={
          <>
            {plan.status === "proposed" && can("fix.approve") ? (
              <>
                <Button onClick={() => setRejecting(true)} disabled={isProposer}>
                  Reject
                </Button>
                <Button variant="primary" onClick={() => approve.mutate()} busy={approve.isPending} disabled={isProposer} data-testid="approve-plan">
                  Approve plan{chosen.length ? ` + ${chosen.length} item(s)` : ""}
                </Button>
              </>
            ) : null}
            {plan.status === "pending_balancing" && can("balancing.approve") ? (
              <Button variant="primary" onClick={() => approveBalancing.mutate()} busy={approveBalancing.isPending}>
                Approve balancing (checker)
              </Button>
            ) : null}
            {plan.status === "approved" && can("fix.approve") ? (
              <Button variant="primary" onClick={() => apply.mutate()} busy={apply.isPending} data-testid="apply-plan">
                Apply and verify
              </Button>
            ) : null}
          </>
        }
      >
        <div className="stack">
          <KeyValue
            items={[
              ["Plan hash", <span className="mono" key="h" title={plan.plan_sha256 ?? ""}>{shortHash(plan.plan_sha256, 20)}</span>],
              ["Proposed", `${formatDateTime(plan.created_at)}${isProposer ? " (by you)" : ""}`],
              ["Approved", plan.approved_at ? `${formatDateTime(plan.approved_at)} · hash ${shortHash(plan.approved_plan_sha256)}` : "—"],
              ["Balancing", plan.has_balancing ? (plan.balancing_approved_by ? "approved" : "needs approval") : "none"],
              ["Edits", `${edits.length} (${edits.filter((e) => e.fix_kind === "auto").length} automatic, ${approvalItems.length} for approval)`],
            ]}
          />
          {plan.status === "planning" ? <Notice>The engine is still planning; this refreshes when it finishes.</Notice> : null}
          {isProposer && plan.status === "proposed" ? <Notice tone="warn">You proposed this plan: a different person must approve it (segregation of duties).</Notice> : null}
          {plan.rejection_reason ? <Notice tone="error">Rejected: {plan.rejection_reason}</Notice> : null}
          {(plan.unresolved ?? []).length ? (
            <Notice tone="warn">
              <strong>Not resolved automatically:</strong>
              <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                {(plan.unresolved as string[]).map((u) => (
                  <li key={u}>{u}</li>
                ))}
              </ul>
            </Notice>
          ) : null}
        </div>
      </Card>
      {applyRun ? <ApplyResult runId={applyRun} onDone={() => void refresh()} /> : null}
      {balancing.length ? (
        <Card title="Balancing (control C3: rounding-sized gaps only)" bodyClass="">
          <DataGrid
            label="Balancing adjustments"
            rows={balancing}
            rowKey={(b) => `${b.statement}-${b.column}-${b.period}`}
            columns={[
              { key: "st", header: "Statement", render: (b) => titleCase(b.statement) },
              { key: "p", header: "Period", render: (b) => b.period ?? b.column },
              { key: "gap", header: "Gap", className: "num", render: (b) => <Amount value={b.gap} /> },
              { key: "t", header: "Balancing line", render: (b) => (b.target ? <span className="mono">{cellRef(b.target.sheet, b.target.addr)}</span> : "—") },
              { key: "adj", header: "Adjustment", className: "num", render: (b) => <Amount value={b.adjustment} /> },
              { key: "s", header: "Status", render: (b) => <Chip tone={b.status === "applied" ? "ok" : "warn"}>{titleCase(b.status)}</Chip> },
              { key: "n", header: "Note", className: "wrap", render: (b) => b.note ?? "" },
            ]}
          />
        </Card>
      ) : null}
      {groups.map(([g, edits]) => (
        <Card key={g} title={`${GROUP_LABEL[g] ?? titleCase(g)} (${edits.length})`} bodyClass="">
          <DataGrid
            label={GROUP_LABEL[g] ?? g}
            rows={edits}
            rowKey={(e) => e.id}
            columns={[
              {
                key: "pick",
                header: "Approve",
                render: (e) =>
                  e.fix_kind === "approval" && plan.status === "proposed" && can("fix.approve") && !isProposer ? (
                    <Checkbox
                      label={<span className="sr-only">Approve {e.eid}</span>}
                      checked={chosen.includes(e.eid)}
                      onChange={(ev) => setChosen((xs) => (ev.target.checked ? [...xs, e.eid] : xs.filter((x) => x !== e.eid)))}
                    />
                  ) : (
                    <Chip tone={e.fix_kind === "auto" ? "accent" : e.fix_kind === "manual" ? undefined : "warn"}>{e.fix_kind}</Chip>
                  ),
              },
              { key: "id", header: "Edit", className: "nowrap", render: (e) => <span className="mono">{e.eid}</span> },
              { key: "cell", header: "Cell", className: "nowrap", render: (e) => <span className="mono">{e.kind.startsWith("hide") ? `${e.sheet} ${e.kind === "hide_row" ? "row" : "col"} ${e.cell}` : cellRef(e.sheet, e.cell)}</span> },
              {
                key: "chg",
                header: "Change",
                className: "wrap",
                render: (e) => (
                  <div className="stack" style={{ gap: 2 }}>
                    {e.old_formula || e.old_value ? <span className="formula change-before">{e.old_formula ? `=${e.old_formula}` : e.old_value}</span> : null}
                    <span className="formula change-after">{e.new_formula ? `=${e.new_formula}` : e.new_value}</span>
                  </div>
                ),
              },
              { key: "why", header: "Reason", className: "wrap", render: (e) => e.reason },
              {
                key: "st",
                header: "",
                render: (e) =>
                  e.applied ? (
                    <Chip tone="ok">applied</Chip>
                  ) : e.approved ? (
                    <Chip tone="accent">approved</Chip>
                  ) : ["approved", "pending_balancing", "applying", "applied"].includes(plan.status) ? (
                    <Chip>not approved</Chip>
                  ) : null,
              },
            ]}
          />
        </Card>
      ))}
      {moves.length ? (
        <Card title={`Printed figures moved (${moves.length})`} bodyClass="">
          <DataGrid
            label="Printed figures moved"
            rows={moves}
            rowKey={(m) => `${m.sheet}!${m.cell}`}
            rowClassName={(m) => (m.is_py ? "highlight-py" : m.is_key ? "highlight-key" : undefined)}
            columns={[
              { key: "cell", header: "Cell", className: "nowrap", render: (m) => <span className="mono">{cellRef(m.sheet, m.cell)}</span> },
              { key: "cap", header: "Caption", className: "wrap", render: (m) => m.caption ?? "" },
              { key: "b", header: "Before", className: "num", render: (m) => <span className="change-before">{m.before}</span> },
              { key: "a", header: "After", className: "num", render: (m) => <span className="change-after">{m.after}</span> },
              { key: "f", header: "", render: (m) => <span className="row">{m.is_py ? <Chip tone="warn">Previous year</Chip> : null}{m.is_key ? <Chip tone="accent">Key figure</Chip> : null}</span> },
            ]}
          />
        </Card>
      ) : null}
      {rejecting ? <RejectDialog plan={plan} onClose={() => setRejecting(false)} onDone={() => void refresh()} /> : null}
    </div>
  );
}

function ApplyResult({ runId, onDone }: { runId: string; onDone: () => void }) {
  const [run, setRun] = useState<RunOut | null>(null);
  const result = (run?.result_json ?? {}) as { verification?: VerificationReport; error?: { verification?: VerificationReport } };
  const verification = result.verification ?? result.error?.verification;
  return (
    <div className="stack">
      <RunWatcher
        runId={runId}
        title="Applying the plan to a new version and verifying it (C5)"
        onDone={(r) => {
          setRun(r);
          onDone();
        }}
      />
      {verification ? (
        <Card title={verification.ok ? "Verification passed" : "Verification failed"}>
          <ul className="stack" style={{ listStyle: "none", padding: 0, margin: 0, gap: 4 }}>
            {verification.steps.map((s) => (
              <li key={s.name}>
                <Chip tone={s.ok ? "ok" : "danger"}>{s.ok ? "✓" : "✗"} {s.name}</Chip> <span className="small">{s.detail}</span>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
    </div>
  );
}

function RejectDialog({ plan, onClose, onDone }: { plan: FixPlanOut; onClose: () => void; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const m = useMutation({
    mutationFn: () => api(`/fix-plans/${plan.id}/reject`, { method: "POST", body: { reason } }),
    onSuccess: () => {
      onDone();
      onClose();
    },
  });
  return (
    <Modal
      title="Reject fix plan"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="danger" busy={m.isPending} disabled={reason.trim().length < 3} onClick={() => m.mutate()}>
            Reject
          </Button>
        </>
      }
    >
      <TextArea label="Reason" value={reason} onChange={(e) => setReason(e.target.value)} />
      <ErrorNotice error={m.error} />
    </Modal>
  );
}
