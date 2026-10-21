/** Friendly aliases for the generated OpenAPI types (src/api/schema.d.ts). */
import type { components } from "./schema";

type S = components["schemas"];

export type MeOut = S["MeOut"];
export type UserOut = S["UserOut"];
export type SessionInfo = S["SessionInfo"];
export type RoleAssignmentOut = S["RoleAssignmentOut"];
export type LoginOut = S["LoginOut"];
export type AuthOkOut = S["AuthOkOut"];
export type MfaEnrollOut = S["MfaEnrollOut"];
export type RecoveryCodesOut = S["RecoveryCodesOut"];
export type OkOut = S["OkOut"];
export type ClientOut = S["ClientOut"];
export type ClientIn = S["ClientIn"];
export type EntityOut = S["EntityOut"];
export type EntityIn = S["EntityIn"];
export type SignatoryOut = S["SignatoryOut"];
export type SignatoryIn = S["SignatoryIn"];
export type EngagementOut = S["EngagementOut"];
export type EngagementIn = S["EngagementIn"];
export type EngagementPatchIn = S["EngagementPatchIn"];
export type GatesOut = S["GatesOut"];
export type GateOut = S["GateOut"];
export type UnlockRequestOut = S["UnlockRequestOut"];
export type ConfigOut = S["ConfigOut"];
export type ChecklistItemOut = S["ChecklistItemOut"];
export type MgmtItemOut = S["MgmtItemOut"];
export type FileOut = S["FileOut"];
export type FileVersionOut = S["FileVersionOut"];
export type UploadOut = S["UploadOut"];
export type CheckoutOut = S["CheckoutOut"];
export type ShareLinkOut = S["ShareLinkOut"];
export type DiffOut = S["DiffOut"];
export type RunOut = S["RunOut"];
export type ArtefactOut = S["ArtefactOut"];
export type FindingOut = S["FindingOut"];
export type FindingPatchIn = S["FindingPatchIn"];
export type RunResultOut = S["RunResultOut"];
export type EditOut = S["EditOut"];
export type FixPlanOut = S["FixPlanOut"];
export type NarrativeEditOut = S["NarrativeEditOut"];
export type ReviewNoteOut = S["ReviewNoteOut"];
export type ReviewNoteMsgOut = S["ReviewNoteMsgOut"];
export type AIPolicyOut = S["AIPolicyOut"];
export type AIRequestOut = S["AIRequestOut"];
export type ReleaseOut = S["ReleaseOut"];
export type AuditEventOut = S["AuditEventOut"];
export type ChainOut = S["ChainOut"];
export type AccessReviewOut = S["AccessReviewOut"];
export type DsrOut = S["DsrOut"];
export type IncidentOut = S["IncidentOut"];
export type BackupLogOut = S["BackupLogOut"];
export type ReleaseRecordOut = S["ReleaseRecordOut"];
export type UsageOut = S["UsageOut"];
export type HealthOut = S["HealthOut"];
export type RuleOut = S["RuleOut"];
export type PolicyOut = S["PolicyOut"];
export type RoleOut = S["RoleOut"];
export type MatrixOut = S["MatrixOut"];
export type InviteOut = S["InviteOut"];
export type ElevationOut = S["ElevationOut"];
export type Page = S["Page"];
export type AuthOptionsOut = S["AuthOptionsOut"];
export type SetupIn = S["SetupIn"];
export type SetupOut = S["SetupOut"];
export type PasswordResetLinkOut = S["PasswordResetLinkOut"];
export type TenantSettingsOut = S["TenantSettingsOut"];
export type BackupSettingsOut = S["BackupSettingsOut"];

/** Workflow states of 06 §1 in order (used for the progress rail). */
export const ENGAGEMENT_STATES = [
  "DRAFT_SETUP",
  "DATA_RECEIVED",
  "CHECKED",
  "FIX_PLANNED",
  "FIX_APPROVED",
  "FIXED_VERIFIED",
  "REVIEW",
  "DRAFT_RELEASED_TO_CLIENT",
  "MGMT_CONFIRMED",
  "FINAL_LOCKED",
  "ARCHIVED",
] as const;

/** Manual transitions exposed by POST /engagements/{id}/transition: [from, to, permission, label]. */
export const MANUAL_TRANSITIONS: [string, string, string, string][] = [
  ["DRAFT_SETUP", "DATA_RECEIVED", "engagement.transition", "Mark data received"],
  ["CHECKED", "REVIEW", "engagement.transition", "Move to review"],
  ["FIXED_VERIFIED", "REVIEW", "engagement.transition", "Move to review"],
  ["REVIEW", "CHECKED", "engagement.transition", "Back to checks"],
  ["DRAFT_RELEASED_TO_CLIENT", "MGMT_CONFIRMED", "release.client", "Mark representations confirmed"],
  ["DRAFT_RELEASED_TO_CLIENT", "REVIEW", "release.client", "Back to review"],
  ["MGMT_CONFIRMED", "FINAL_LOCKED", "engagement.lock", "Mark FINAL and lock"],
  ["FINAL_LOCKED", "ARCHIVED", "engagement.lock", "Archive"],
];

/** Shapes inside run.result_json / fix plans that the OpenAPI schema types as `any`. */
export interface KeyFigure {
  key: string;
  label: string;
  period: string;
  value: string | null;
  printed: string | null;
  ref: { sheet: string; addr: string } | null;
}

export interface Evidence {
  label: string;
  ref?: { sheet: string; addr: string } | null;
  printed?: string | null;
  value?: string | null;
  note?: string | null;
}

export interface BalancingAdjustment {
  statement: string;
  column: string;
  period?: string | null;
  gap: string;
  target?: { sheet: string; addr: string } | null;
  adjustment: string;
  status: string;
  note?: string;
}

export interface PrintedMove {
  sheet: string;
  cell: string;
  caption?: string | null;
  before?: string | null;
  after?: string | null;
  period?: string | null;
  is_py?: boolean;
  is_key?: boolean;
}

export interface VerificationStep {
  name: string;
  ok: boolean;
  detail?: string;
}

export interface VerificationReport {
  ok: boolean;
  steps: VerificationStep[];
  unexpected_changes?: string[];
  xml_errors?: string[];
  remaining_cast_plug?: number;
  selftest_match_rate?: number;
}

export interface PdfMiss {
  sheet: string;
  cell: string;
  printed: string;
  caption?: string | null;
  pages?: number[];
}

export interface PdfRecon {
  pdf_sha256?: string;
  pages?: number;
  page_map?: { page: number; sheet: string | null; score: number }[];
  values_checked?: number;
  misses?: PdfMiss[];
  stale?: boolean;
  error_tokens?: string[];
  text_mismatches?: string[];
  reprint_pages?: number[];
  blank_pages?: number[];
}

/** A typed-text correction the engine proposes (GC-06 …); `text_proposals` of GET /runs/{id}/result. */
export interface TextProposal {
  sheet: string;
  cell: string;
  new_text: string;
  reason?: string;
}

export interface RptRow {
  counterparty: string;
  nature: string;
  period: string;
  rpt_value: string | null;
  rpt_cell: string | null;
  rpt_party_as_written?: string | null;
  tb_value: string | null;
  tb_accounts: string[];
  fs_value: string | null;
  fs_cell: string | null;
  variance: string | null;
  status: string;
  flags: string[];
}

export interface ChangeRow {
  sheet: string;
  cell: string;
  kind: string;
  before?: string | null;
  after?: string | null;
}
