/** Disclosure checklist (E-PREP-02): auto-answers from the latest check, manual answers, completion. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import { qk } from "@/api/hooks";
import type { ChecklistItemOut } from "@/api/types";
import { useToast } from "@/components/Toast";
import { Button, Card, Chip, ErrorNotice, ProgressBar, Spinner } from "@/components/ui";
import { formatDateTime } from "@/lib/format";

import type { TabProps } from "../tabKeys";

export function ChecklistTab({ eng, can }: TabProps) {
  const qc = useQueryClient();
  const toast = useToast();
  const items = useQuery({ queryKey: qk.checklist(eng.id), queryFn: () => api<ChecklistItemOut[]>(`/engagements/${eng.id}/disclosure-checklist`) });
  const answer = useMutation({
    mutationFn: ({ code, value }: { code: string; value: string }) =>
      api<ChecklistItemOut>(`/engagements/${eng.id}/disclosure-checklist/${encodeURIComponent(code)}`, { method: "PUT", body: { answer: value } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.checklist(eng.id) }),
    onError: (e) => toast.error(e),
  });
  const auto = useMutation({
    mutationFn: () => api<ChecklistItemOut[]>(`/engagements/${eng.id}/disclosure-checklist/auto`, { method: "POST" }),
    onSuccess: async () => {
      toast.ok("Answered what the engine could detect");
      await qc.invalidateQueries({ queryKey: qk.checklist(eng.id) });
    },
    onError: (e) => toast.error(e, "Auto-answer not possible"),
  });
  const list = items.data ?? [];
  const answered = list.filter((i) => i.answer).length;
  const editable = can("disclosure.edit") && !["FINAL_LOCKED", "ARCHIVED"].includes(eng.status);
  return (
    <Card
      title={`Disclosure checklist — ${answered} of ${list.length} answered`}
      actions={editable ? <Button onClick={() => auto.mutate()} busy={auto.isPending}>Auto-answer from latest check</Button> : undefined}
    >
      <div className="stack">
        <ProgressBar value={list.length ? answered / list.length : 0} label={answered === list.length && list.length ? "Complete — this gate is met for FINAL_LOCKED" : "Unanswered items block FINAL_LOCKED"} />
        {items.isLoading ? <Spinner /> : null}
        <ErrorNotice error={items.error} />
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Item</th>
                <th>Requirement</th>
                <th>Answer</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {list.map((i) => (
                <tr key={i.id}>
                  <td className="mono">{i.item_code}</td>
                  <td className="wrap">{i.question}</td>
                  <td>
                    {editable ? (
                      <select aria-label={`Answer for ${i.item_code}`} value={i.answer ?? ""} onChange={(e) => e.target.value && answer.mutate({ code: i.item_code, value: e.target.value })} style={{ width: "auto" }}>
                        <option value="">— unanswered —</option>
                        <option value="yes">Yes</option>
                        <option value="no">No</option>
                        <option value="na">Not applicable</option>
                      </select>
                    ) : (
                      i.answer ?? "—"
                    )}
                  </td>
                  <td className="small">
                    {i.auto ? <Chip tone="accent">auto</Chip> : i.answered_at ? <Chip>manual</Chip> : null} {i.answered_at ? formatDateTime(i.answered_at) : ""}
                    {i.evidence_ref ? <div className="mono faint">{i.evidence_ref}</div> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </Card>
  );
}
