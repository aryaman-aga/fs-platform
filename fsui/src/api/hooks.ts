/** TanStack Query hooks shared by several screens. */
import { useQuery } from "@tanstack/react-query";

import { api } from "./client";
import type {
  ClientOut,
  EngagementOut,
  FileOut,
  FindingOut,
  FixPlanOut,
  GatesOut,
  ReviewNoteOut,
  RunOut,
  RunResultOut,
} from "./types";

export const qk = {
  me: ["me"] as const,
  engagements: (filter?: Record<string, string | undefined>) => ["engagements", filter ?? {}] as const,
  engagement: (id: string) => ["engagement", id] as const,
  gates: (id: string) => ["engagement", id, "gates"] as const,
  files: (id: string) => ["engagement", id, "files"] as const,
  versions: (fileId: string) => ["file", fileId, "versions"] as const,
  runs: (id: string, kind?: string) => ["engagement", id, "runs", kind ?? "all"] as const,
  run: (runId: string) => ["run", runId] as const,
  runResult: (runId: string) => ["run", runId, "result"] as const,
  findings: (runId: string) => ["run", runId, "findings"] as const,
  artefacts: (runId: string) => ["run", runId, "artefacts"] as const,
  plans: (id: string) => ["engagement", id, "fix-plans"] as const,
  plan: (planId: string) => ["fix-plan", planId] as const,
  notes: (id: string) => ["engagement", id, "review-notes"] as const,
  narrative: (id: string) => ["engagement", id, "narrative-edits"] as const,
  timeline: (id: string) => ["engagement", id, "timeline"] as const,
  config: (id: string) => ["engagement", id, "config"] as const,
  checklist: (id: string) => ["engagement", id, "checklist"] as const,
  mgmt: (id: string) => ["engagement", id, "mgmt-items"] as const,
  releases: (id: string) => ["engagement", id, "releases"] as const,
  ai: (id: string) => ["engagement", id, "ai-requests"] as const,
  unlocks: (id: string) => ["engagement", id, "unlock-requests"] as const,
  clients: ["clients"] as const,
  client: (id: string) => ["client", id] as const,
};

export function useEngagements(filter?: { status?: string; client_id?: string }) {
  return useQuery({
    queryKey: qk.engagements(filter),
    queryFn: () => api<EngagementOut[]>("/engagements", { query: filter }),
  });
}

export function useEngagement(id: string | undefined) {
  return useQuery({
    queryKey: qk.engagement(id ?? ""),
    queryFn: () => api<EngagementOut>(`/engagements/${id}`),
    enabled: !!id,
  });
}

export function useGates(id: string, enabled = true) {
  return useQuery({ queryKey: qk.gates(id), queryFn: () => api<GatesOut>(`/engagements/${id}/gates`), enabled });
}

export function useFiles(id: string) {
  return useQuery({ queryKey: qk.files(id), queryFn: () => api<FileOut[]>(`/engagements/${id}/files`) });
}

export function useRuns(id: string, kind?: string) {
  return useQuery({
    queryKey: qk.runs(id, kind),
    queryFn: () => api<RunOut[]>(`/engagements/${id}/runs`, { query: { kind, limit: 100 } }),
  });
}

export function useRunResult(runId: string | undefined) {
  return useQuery({
    queryKey: qk.runResult(runId ?? ""),
    queryFn: () => api<RunResultOut>(`/runs/${runId}/result`),
    enabled: !!runId,
  });
}

export function useFindings(runId: string | undefined) {
  return useQuery({
    queryKey: qk.findings(runId ?? ""),
    queryFn: () => api<FindingOut[]>(`/runs/${runId}/findings`),
    enabled: !!runId,
  });
}

export function useFixPlans(id: string) {
  return useQuery({ queryKey: qk.plans(id), queryFn: () => api<FixPlanOut[]>(`/engagements/${id}/fix-plans`) });
}

export function useReviewNotes(id: string) {
  return useQuery({ queryKey: qk.notes(id), queryFn: () => api<ReviewNoteOut[]>(`/engagements/${id}/review-notes`) });
}

export function useClients(enabled = true) {
  return useQuery({ queryKey: qk.clients, queryFn: () => api<ClientOut[]>("/clients"), enabled });
}

/** The most recent successful run of a kind (runs are listed newest first). */
export function latestSucceeded(runs: RunOut[] | undefined, kind: string): RunOut | undefined {
  return (runs ?? []).find((r) => r.kind === kind && r.status === "succeeded");
}
