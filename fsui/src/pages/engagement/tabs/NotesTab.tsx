/** Review notes (06 §3): threads; Partner / Reviewer clear, the preparer responds; CSV export. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, apiBlob, saveFile } from "@/api/client";
import { qk, useReviewNotes } from "@/api/hooks";
import type { ReviewNoteOut } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/components/Toast";
import { Button, Card, Chip, EmptyState, ErrorNotice, SelectInput, Spinner, TextArea, TextInput } from "@/components/ui";
import { formatDateTime, titleCase } from "@/lib/format";

import type { TabProps } from "../tabKeys";

export function NotesTab({ eng, can }: TabProps) {
  const qc = useQueryClient();
  const toast = useToast();
  const { me } = useAuth();
  const notes = useReviewNotes(eng.id);
  const [filter, setFilter] = useState("open");
  const [text, setText] = useState("");
  const [targetType, setTargetType] = useState("engagement");
  const [targetRef, setTargetRef] = useState("");
  const [assignee, setAssignee] = useState("");
  const raise = useMutation({
    mutationFn: () =>
      api<ReviewNoteOut>(`/engagements/${eng.id}/review-notes`, {
        method: "POST",
        body: { target_type: targetType, target_ref: targetRef || null, text, assigned_to: assignee || null },
      }),
    onSuccess: async () => {
      setText("");
      setTargetRef("");
      toast.ok("Review note raised");
      await qc.invalidateQueries({ queryKey: qk.notes(eng.id) });
    },
    onError: (e) => toast.error(e, "Not raised"),
  });
  const list = (notes.data ?? []).filter((n) => (filter === "open" ? n.status !== "Cleared" : filter === "all" ? true : n.status === filter));
  const exportCsv = async () => {
    try {
      saveFile(await apiBlob(`/engagements/${eng.id}/review-notes/export`, {}, "review-notes.csv"));
    } catch (e) {
      toast.error(e);
    }
  };

  return (
    <div className="split">
      <div className="stack">
        <div className="row between">
          <select aria-label="Filter review notes" value={filter} onChange={(e) => setFilter(e.target.value)} style={{ width: "auto" }}>
            <option value="open">Open and responded</option>
            <option value="Open">Open</option>
            <option value="Responded">Responded</option>
            <option value="Cleared">Cleared</option>
            <option value="all">All</option>
          </select>
          <Button size="sm" onClick={() => void exportCsv()}>
            ⤓ Export with clearance evidence
          </Button>
        </div>
        {notes.isLoading ? <Spinner /> : null}
        <ErrorNotice error={notes.error} />
        {list.length === 0 && !notes.isLoading ? <EmptyState>No review notes here.</EmptyState> : null}
        {list.map((n) => (
          <NoteThread key={n.id} note={n} can={can} meId={me?.user.id} engagementId={eng.id} />
        ))}
      </div>
      {can("review.raise") ? (
        <Card title="Raise a review note">
          <div className="stack">
            <SelectInput
              label="About"
              value={targetType}
              onChange={(e) => setTargetType(e.target.value)}
              options={[
                { value: "engagement", label: "The engagement" },
                { value: "finding", label: "A finding (id)" },
                { value: "cell", label: "A cell (Sheet!A1)" },
                { value: "pdf_page", label: "A PDF page" },
              ]}
            />
            {targetType !== "engagement" ? <TextInput label="Reference" value={targetRef} onChange={(e) => setTargetRef(e.target.value)} placeholder={targetType === "cell" ? "BS!C12" : targetType === "pdf_page" ? "page 4" : "finding id"} /> : null}
            <SelectInput
              label="Assign to"
              value={assignee}
              onChange={(e) => setAssignee(e.target.value)}
              options={[
                { value: "", label: "— unassigned —" },
                ...(eng.manager_id ? [{ value: eng.manager_id, label: "Engagement manager" }] : []),
                ...(eng.partner_id ? [{ value: eng.partner_id, label: "Engagement partner" }] : []),
                ...(me ? [{ value: me.user.id, label: "Me" }] : []),
              ]}
            />
            <TextArea label="Note" value={text} onChange={(e) => setText(e.target.value)} />
            <Button variant="primary" onClick={() => raise.mutate()} busy={raise.isPending} disabled={!text.trim()}>
              Raise note
            </Button>
          </div>
        </Card>
      ) : null}
    </div>
  );
}

function NoteThread({ note, can, meId, engagementId }: { note: ReviewNoteOut; can: (p: string) => boolean; meId?: string; engagementId: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [reply, setReply] = useState("");
  const respond = useMutation({
    mutationFn: () => api<ReviewNoteOut>(`/review-notes/${note.id}/messages`, { method: "POST", body: { text: reply } }),
    onSuccess: async () => {
      setReply("");
      await qc.invalidateQueries({ queryKey: qk.notes(engagementId) });
    },
    onError: (e) => toast.error(e, "Not sent"),
  });
  const clear = useMutation({
    mutationFn: () => api<ReviewNoteOut>(`/review-notes/${note.id}/clear`, { method: "POST", ifMatch: `W/"${note.version}"` }),
    onSuccess: async () => {
      toast.ok("Note cleared");
      await qc.invalidateQueries({ queryKey: ["engagement", engagementId] });
    },
    onError: (e) => toast.error(e, "Not cleared"),
  });
  const tone = note.status === "Cleared" ? "ok" : note.status === "Responded" ? "accent" : "warn";
  const canClear = note.audience === "client" ? can("release.client") : can("review.clear");
  return (
    <Card
      title={
        <div className="row">
          <Chip tone={tone}>{note.status}</Chip>
          <span className="small muted">
            {titleCase(note.target_type)}
            {note.target_ref ? ` · ${note.target_ref}` : ""} · {note.audience === "client" ? "client query" : "team"} · {formatDateTime(note.created_at)}
            {note.assigned_to === meId ? " · assigned to you" : ""}
          </span>
        </div>
      }
      actions={note.status !== "Cleared" && canClear ? <Button size="sm" onClick={() => clear.mutate()} busy={clear.isPending}>Clear</Button> : undefined}
    >
      <div className="stack">
        <p style={{ margin: 0, whiteSpace: "pre-wrap" }}>{note.text}</p>
        {(note.messages ?? []).map((m) => (
          <div key={m.id} className="sentence small">
            <div className="muted">{m.user_id === meId ? "You" : "Team member"} · {formatDateTime(m.created_at)}</div>
            <div style={{ whiteSpace: "pre-wrap" }}>{m.text}</div>
          </div>
        ))}
        {note.status !== "Cleared" && can("review.respond") ? (
          <div className="row" style={{ alignItems: "flex-end" }}>
            <div style={{ flex: 1 }}>
              <TextArea label="Respond" value={reply} onChange={(e) => setReply(e.target.value)} style={{ minHeight: 50 }} />
            </div>
            <Button onClick={() => respond.mutate()} busy={respond.isPending} disabled={!reply.trim()}>
              Send
            </Button>
          </div>
        ) : null}
      </div>
    </Card>
  );
}
