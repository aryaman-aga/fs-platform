import { useRunProgress } from "@/api/runs";
import type { RunOut } from "@/api/types";

import { Notice, ProgressBar, RunStatusChip } from "./ui";

/** Live progress of one run (SSE with polling fallback); calls onDone once when it ends. */
export function RunWatcher({ runId, title, onDone }: { runId: string; title: string; onDone?: (run: RunOut) => void }) {
  const p = useRunProgress(runId, onDone);
  return (
    <div className="card card-body stack" aria-live="polite" data-testid="run-watcher">
      <div className="row between">
        <strong>{title}</strong>
        <RunStatusChip status={p.done ? (p.run?.status ?? p.status) : p.status === "queued" ? "queued" : "running"} />
      </div>
      <ProgressBar value={p.done ? 1 : p.pct} label={p.message} />
      {p.done && p.run?.status === "failed" ? (
        <Notice tone="error">
          {p.run.error_message || p.run.error_code || "The run failed."}
          {p.run.correlation_id ? <span className="cid">ref {p.run.correlation_id}</span> : null}
        </Notice>
      ) : null}
    </div>
  );
}
