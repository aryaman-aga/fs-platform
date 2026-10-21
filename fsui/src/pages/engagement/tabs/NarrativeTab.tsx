/** Narrative notes (Going Concern etc.): quoted figures green / red; typed-text corrections with approval (C6). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { api } from "@/api/client";
import { latestSucceeded, qk, useFiles, useRunResult, useRuns } from "@/api/hooks";
import type { NarrativeEditOut, TextProposal } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { DataGrid } from "@/components/DataGrid";
import { RunWatcher } from "@/components/RunWatcher";
import { useToast } from "@/components/Toast";
import { Button, Card, Chip, EmptyState, ErrorNotice, Notice, Spinner, TextArea, TextInput } from "@/components/ui";
import { cellRef } from "@/lib/cells";
import { formatDateTime, titleCase } from "@/lib/format";
import { isCellAddress, validateNarrativeText } from "@/lib/formula";
import type { Mention, NarrativeBlock } from "@/lib/narrative";
import { classifyStatus, groupMentions, highlightSentence, suggestCorrection } from "@/lib/narrative";

import type { TabProps } from "../tabKeys";

function Highlighted({ sentence, mentions }: { sentence: string; mentions: Mention[] }) {
  return (
    <p className="sentence" style={{ margin: 0 }}>
      {highlightSentence(sentence, mentions).map((seg, i) =>
        seg.status ? (
          <mark
            key={i}
            className={`fig ${seg.status}`}
            title={
              seg.status === "matched"
                ? "Matches the statements"
                : `${String(seg.mention?.status ?? "Mismatch")}${seg.mention?.expected_printed || seg.mention?.expected ? ` — statements show ${String(seg.mention?.expected_printed ?? seg.mention?.expected)}` : ""}`
            }
          >
            {seg.text}
            <span className="sr-only">{seg.status === "matched" ? " (matches)" : " (does not match)"}</span>
          </mark>
        ) : (
          <span key={i}>{seg.text}</span>
        ),
      )}
    </p>
  );
}

export function NarrativeTab({ eng, can }: TabProps) {
  const qc = useQueryClient();
  const toast = useToast();
  const { me } = useAuth();
  const runs = useRuns(eng.id, "check");
  const latest = latestSucceeded(runs.data, "check");
  const result = useRunResult(latest?.id);
  const files = useFiles(eng.id);
  const workbook = (files.data ?? []).find((f) => f.kind === "workbook");
  const edits = useQuery({ queryKey: qk.narrative(eng.id), queryFn: () => api<NarrativeEditOut[]>(`/engagements/${eng.id}/narrative-edits`) });
  const blocks = useMemo(() => groupMentions((result.data?.narrative ?? []) as Mention[]), [result.data]);
  // the engine's own typed-text proposals win over the wording suggested here from the mentions
  const proposals = useMemo(() => new Map(((result.data?.text_proposals ?? []) as TextProposal[]).map((t) => [`${t.sheet}!${t.cell}`, t])), [result.data]);
  const unmatched = [...proposals.values()].filter((t) => !blocks.some((b) => b.sheet === t.sheet && b.cell === t.cell));
  const [selected, setSelected] = useState<NarrativeBlock | null>(null);
  const [sheet, setSheet] = useState("");
  const [cell, setCell] = useState("");
  const [text, setText] = useState("");
  const [reason, setReason] = useState("");
  const [applyRun, setApplyRun] = useState<string | null>(null);
  const check = validateNarrativeText(text);
  const cellOk = isCellAddress(cell);

  const pick = (b: NarrativeBlock) => {
    const engine = proposals.get(`${b.sheet}!${b.cell}`);
    setSelected(b);
    setSheet(b.sheet);
    setCell(b.cell);
    setText(engine?.new_text ?? b.sentences.map((s) => suggestCorrection(s.sentence, s.mentions)).join(" "));
    setReason(engine?.reason || (b.mismatches ? "Figures updated to agree with the statements (GC-06)" : ""));
  };
  const takeProposal = (t: TextProposal) => {
    setSelected(null);
    setSheet(t.sheet);
    setCell(t.cell);
    setText(t.new_text);
    setReason(t.reason ?? "");
  };

  const propose = useMutation({
    mutationFn: () =>
      api<NarrativeEditOut>(`/engagements/${eng.id}/narrative-edits`, {
        method: "POST",
        body: {
          sheet,
          cell,
          new_text: text,
          old_text: selected ? selected.sentences.map((s) => s.sentence).join(" ") : null,
          reason,
          base_version_id: workbook?.latest_version?.id ?? null,
        },
      }),
    onSuccess: async (ne) => {
      toast.ok(ne.status === "pending_approval" ? "Submitted — a Manager must approve it" : "Edit recorded and approved");
      await qc.invalidateQueries({ queryKey: qk.narrative(eng.id) });
    },
    onError: (e) => toast.error(e, "Edit refused"),
  });
  const decide = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "approve" | "reject" }) =>
      api<NarrativeEditOut>(`/narrative-edits/${id}/${action}`, { method: "POST", body: action === "reject" ? { reason: "Rejected by reviewer" } : undefined }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.narrative(eng.id) }),
    onError: (e) => toast.error(e),
  });
  const apply = useMutation({
    mutationFn: () => api<{ run_id: string; edit_ids: string[] }>(`/engagements/${eng.id}/narrative-edits/apply`, { method: "POST" }),
    onSuccess: (r) => setApplyRun(r.run_id),
    onError: (e) => toast.error(e, "Not applied"),
  });
  const approved = (edits.data ?? []).filter((e) => e.status === "approved").length;

  return (
    <div className="stack">
      <Notice>
        Corrections are written as <strong>typed text in 2 decimals</strong>, never formulas (control C6). Staff edits need a Manager's approval.
      </Notice>
      <div className="split">
        <Card title="Narrative in the workbook" bodyClass="card-body stack">
          {!latest ? (
            <EmptyState>Run checks to analyse the narrative notes.</EmptyState>
          ) : result.isLoading ? (
            <Spinner />
          ) : blocks.length === 0 ? (
            <EmptyState>No figures quoted in narrative text were found.</EmptyState>
          ) : (
            blocks.map((b) => (
              <div key={b.key} className="stack" style={{ gap: 6 }}>
                <div className="row between">
                  <span className="mono small">{cellRef(b.sheet, b.cell)}</span>
                  <span className="row">
                    {proposals.has(`${b.sheet}!${b.cell}`) ? <Chip tone="accent">engine proposal</Chip> : null}
                    {b.mismatches ? <Chip tone="danger">{b.mismatches} mismatch(es)</Chip> : <Chip tone="ok">all match</Chip>}
                    {can("narrative.edit") ? (
                      <Button size="sm" onClick={() => pick(b)}>
                        Correct
                      </Button>
                    ) : null}
                  </span>
                </div>
                {b.sentences.map((s, i) => (
                  <Highlighted key={i} sentence={s.sentence} mentions={s.mentions} />
                ))}
                <ul className="small muted" style={{ margin: 0, paddingLeft: 18 }}>
                  {b.sentences
                    .flatMap((s) => s.mentions)
                    .filter((m) => classifyStatus(m.status) === "mismatch")
                    .map((m, i) => (
                      <li key={i}>
                        {String(m.metric ?? "figure")} {m.period ? `(${String(m.period)})` : ""}: text says “{String(m.quoted ?? m.quoted_text ?? m.raw ?? "")}”, statements show{" "}
                        <strong>{String(m.expected_printed ?? m.expected ?? "?")}</strong> — {String(m.status)}
                      </li>
                    ))}
                </ul>
              </div>
            ))
          )}
          {unmatched.length ? (
            <div className="stack" style={{ gap: 6 }}>
              <strong className="small">Other corrections the engine proposes</strong>
              {unmatched.map((t) => (
                <div key={`${t.sheet}!${t.cell}`} className="sentence stack" style={{ gap: 4 }}>
                  <div className="row between">
                    <span className="mono small">{cellRef(t.sheet, t.cell)}</span>
                    {can("narrative.edit") ? (
                      <Button size="sm" onClick={() => takeProposal(t)}>
                        Use
                      </Button>
                    ) : null}
                  </div>
                  <span>{t.new_text}</span>
                  {t.reason ? <span className="small muted">{t.reason}</span> : null}
                </div>
              ))}
            </div>
          ) : null}
        </Card>
        {can("narrative.edit") ? (
          <Card title="Proposed typed text" bodyClass="card-body stack">
            <div className="grid-2">
              <TextInput label="Sheet" value={sheet} onChange={(e) => setSheet(e.target.value)} />
              <TextInput label="Cell" value={cell} onChange={(e) => setCell(e.target.value.toUpperCase())} error={cell && !cellOk ? "Use a cell address such as B12" : undefined} />
            </div>
            <TextArea label="Corrected text" value={text} onChange={(e) => setText(e.target.value)} style={{ minHeight: 200 }} error={text && !check.ok ? check.message : undefined} />
            <TextInput label="Reason" value={reason} onChange={(e) => setReason(e.target.value)} />
            <div className="row">
              <Button variant="primary" busy={propose.isPending} disabled={!check.ok || !cellOk || !sheet} onClick={() => propose.mutate()}>
                Submit for approval
              </Button>
            </div>
          </Card>
        ) : null}
      </div>
      <Card
        title="Narrative edits"
        bodyClass=""
        actions={
          approved && can("narrative.edit") ? (
            <Button variant="primary" onClick={() => apply.mutate()} busy={apply.isPending}>
              Apply {approved} approved edit(s) as a new version
            </Button>
          ) : undefined
        }
      >
        {applyRun ? (
          <div className="card-body">
            <RunWatcher runId={applyRun} title="Writing typed text to a new version" onDone={() => void qc.invalidateQueries({ queryKey: ["engagement", eng.id] })} />
          </div>
        ) : null}
        <ErrorNotice error={edits.error} />
        <DataGrid
          label="Narrative edits"
          rows={edits.data ?? []}
          rowKey={(e) => e.id}
          empty="No narrative edits yet."
          columns={[
            { key: "cell", header: "Cell", render: (e) => <span className="mono">{cellRef(e.sheet, e.cell)}</span> },
            { key: "text", header: "New text", className: "wrap", render: (e) => e.new_text },
            { key: "why", header: "Reason", className: "wrap", render: (e) => e.reason },
            { key: "st", header: "Status", render: (e) => <Chip tone={e.status === "approved" ? "accent" : e.status === "applied" ? "ok" : e.status === "rejected" ? "danger" : "warn"}>{titleCase(e.status)}</Chip> },
            { key: "when", header: "Proposed", render: (e) => formatDateTime(e.created_at) },
            {
              key: "act",
              header: "",
              render: (e) =>
                e.status === "pending_approval" && can("narrative.edit") && e.proposed_by !== me?.user.id ? (
                  <span className="row">
                    <Button size="sm" onClick={() => decide.mutate({ id: e.id, action: "approve" })}>
                      Approve
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => decide.mutate({ id: e.id, action: "reject" })}>
                      Reject
                    </Button>
                  </span>
                ) : null,
            },
          ]}
        />
      </Card>
    </div>
  );
}
