/**
 * Run progress: Server-Sent Events from /runs/{id}/events (04 §5 step 4), with a
 * polling fallback when EventSource is unavailable or the stream drops.
 */
import { useEffect, useRef, useState } from "react";

import { API_BASE, api } from "./client";
import type { RunOut } from "./types";

export const TERMINAL = new Set(["succeeded", "failed"]);

export interface RunEvent {
  id: number;
  kind: string;
  message: string;
  pct: number;
  ts: string;
}

export interface RunProgress {
  status: string;
  pct: number;
  message: string;
  events: RunEvent[];
  done: boolean;
  run?: RunOut;
  error?: string;
}

const INITIAL: RunProgress = { status: "queued", pct: 0, message: "queued", events: [], done: false };

/**
 * Follow a run until it finishes. `onDone` fires once with the final run record.
 *
 * The run is read first: a run that already finished needs no stream, and the
 * stream opens only once the API has answered for this run. (On SQLite the SSE
 * endpoint reads the run on the server's event loop; opened while the request
 * that created the run still holds the write lock, it stalls the API for the
 * 30-second busy timeout.)
 */
export function useRunProgress(runId: string | null | undefined, onDone?: (run: RunOut) => void): RunProgress {
  const [state, setState] = useState<RunProgress>(INITIAL);
  const doneRef = useRef(onDone);
  doneRef.current = onDone;

  useEffect(() => {
    if (!runId) {
      setState(INITIAL);
      return;
    }
    let cancelled = false;
    let reported = false;
    let es: EventSource | null = null;
    let pollTimer: number | undefined;
    setState({ ...INITIAL });

    const report = (run: RunOut) => {
      if (cancelled || reported) return;
      reported = true;
      setState((s) => ({
        ...s,
        status: run.status,
        pct: 1,
        message: run.status === "failed" ? `failed: ${run.error_code ?? ""} ${run.error_message ?? ""}`.trim() : "completed",
        done: true,
        run,
        error: run.status === "failed" ? run.error_message ?? run.error_code ?? "failed" : undefined,
      }));
      doneRef.current?.(run);
    };

    // polling: the fallback when EventSource is unavailable or the stream drops
    const poll = async () => {
      try {
        const run = await api<RunOut>(`/runs/${runId}`);
        if (cancelled || reported) return;
        if (TERMINAL.has(run.status)) report(run);
        else pollTimer = window.setTimeout(() => void poll(), 800);
      } catch (e) {
        if (!cancelled) setState((s) => ({ ...s, done: true, error: (e as Error).message }));
      }
    };

    const stream = () => {
      es = new EventSource(`${API_BASE}/runs/${runId}/events`, { withCredentials: true });
      const onEvent = (ev: MessageEvent<string>) => {
        try {
          const data = JSON.parse(ev.data) as RunEvent;
          setState((s) => ({
            ...s,
            status: data.kind === "status" ? data.message : s.status === "queued" ? "running" : s.status,
            pct: Math.max(s.pct, Number(data.pct) || 0),
            message: data.message,
            events: [...s.events, data].slice(-50),
          }));
        } catch {
          // ignore malformed event
        }
      };
      es.addEventListener("progress", onEvent as EventListener);
      es.addEventListener("status", onEvent as EventListener);
      const stop = () => {
        es?.close();
        es = null;
        void poll(); // the final record (and a fallback when the stream dropped mid-run)
      };
      es.addEventListener("end", stop);
      es.onerror = stop;
    };

    void (async () => {
      try {
        const run = await api<RunOut>(`/runs/${runId}`);
        if (cancelled) return;
        if (TERMINAL.has(run.status)) return report(run);
        setState((s) => ({ ...s, status: run.status }));
      } catch {
        // the stream / polling below report the error
      }
      if (cancelled) return;
      if (typeof EventSource === "undefined") void poll();
      else stream();
    })();

    return () => {
      cancelled = true;
      es?.close();
      if (pollTimer) window.clearTimeout(pollTimer);
    };
  }, [runId]);

  return state;
}

export function newIdempotencyKey(prefix = "ui"): string {
  const rnd = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : Math.random().toString(36).slice(2);
  return `${prefix}-${rnd}`;
}
