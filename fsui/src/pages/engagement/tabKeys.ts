import type { EngagementOut } from "@/api/types";

export const TAB_KEYS = ["files", "checks", "fix", "narrative", "rpt", "pdf", "notes", "client", "checklist", "ai", "audit", "settings"] as const;
export type TabKey = (typeof TAB_KEYS)[number];

export interface TabProps {
  eng: EngagementOut;
  can: (permission: string) => boolean;
}
