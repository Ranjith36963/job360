// ---------------------------------------------------------------------------
// Job360 Frontend — TypeScript types
//
// Generated aliases: names aliased from the backend OpenAPI schema
// (TailoredDocOut / TailorBundle narrow doc_kind/status to literal unions).
// Frontend-only: ProfileVersionDiff, PreferencesRequest, SkillProvenance,
//                SkillTiers, TailorDocKind
// ---------------------------------------------------------------------------

import type { components } from "./api-types";

type Schemas = components["schemas"];

// ---- Generated aliases ----

export type ApplicationTimelineResponse = Schemas["ApplicationTimelineResponse"];
export type BringJobRequest = Schemas["BringJobRequest"];
export type BringJobResponse = Schemas["BringJobResponse"];
export type CreateReceiptRequest = Schemas["CreateReceiptRequest"];
export type CVDetail = Schemas["CVDetail"];
export type HealthResponse = Schemas["HealthResponse"];
export type JsonResumeResponse = Schemas["JsonResumeResponse"];
export type NotificationLedgerEntry = Schemas["NotificationLedgerEntry"];
export type NotificationLedgerListResponse = Schemas["NotificationLedgerListResponse"];
export type NotificationRule = Schemas["NotificationRule"];
export type NotificationRuleUpdate = Schemas["NotificationRuleUpdate"];
export type PipelineAdvanceRequest = Schemas["PipelineAdvanceRequest"];
export type PipelineApplication = Schemas["PipelineApplication"];
export type ProfileResponse = Schemas["ProfileResponse"];
export type ProfileSummary = Schemas["ProfileSummary"];
export type ProfileVersionsListResponse = Schemas["ProfileVersionsListResponse"];
export type Receipt = Schemas["Receipt"];
export type ReceiptListResponse = Schemas["ReceiptListResponse"];
export type ReceiptSummary = Schemas["ReceiptSummary"];
export type ProfileVersionSummary = Schemas["ProfileVersionSummary"];
export type TimelineEntry = Schemas["TimelineEntry"];

// `doc_kind`/`status` are narrowed from `string` to literal unions — the
// backend OpenAPI declares them as string but only these values are valid
// (docs/product/peruser_cv_coverletter.md). Same pattern as `Channel` in api.ts.
export type TailorDocKind = "cv" | "cover_letter";

export type TailoredDocOut = Omit<Schemas["TailoredDocOut"], "doc_kind" | "status"> & {
  doc_kind: TailorDocKind;
  status: "draft" | "kept";
};

export type TailorBundle = Omit<Schemas["TailorBundle"], "documents"> & {
  documents: TailoredDocOut[];
};

// ---- Frontend-only types (NOT in the backend schema) ----

// ---- Step-3: Profile version diff (C-06) — not in backend OpenAPI schema ----

export interface ProfileVersionDiff {
  version_id1: number;
  version_id2: number;
  changes: Record<string, { from: unknown; to: unknown }>;
  changed_fields: string[];
}

// ---- Frontend-only preferences shape (sent as multipart form JSON) ----

export interface PreferencesRequest {
  target_job_titles?: string[];
  additional_skills?: string[];
  excluded_skills?: string[];
  preferred_locations?: string[];
  industries?: string[];
  salary_min?: number | null;
  salary_max?: number | null;
  work_arrangement?: string;
  experience_level?: string;
  negative_keywords?: string[];
  about_me?: string;
  excluded_companies?: string[];
  // Whether the user needs visa sponsorship. Gates the VISA scoring dimension
  // (weight 6) on the backend. Had no UI control until 2026-08-08, so the
  // dimension could never fire for anyone — the field the scorer reads was
  // always the default False because no screen could set it.
  needs_visa?: boolean;
}

// ---- Skill tier / provenance — frontend structuring of profile fields ----
//
// The backend ProfileResponse returns skill_tiers / skill_provenance as
// generic { [key: string]: string[] } dicts.  These interfaces give the
// frontend a typed view of the well-known keys.  Cast via the helpers on the
// profile page: (profile.skill_tiers as unknown as SkillTiers | null)

export interface SkillTiers {
  primary: string[];
  secondary: string[];
  tertiary: string[];
}

export interface SkillProvenance {
  cv: string[];
  linkedin: string[];
  github: string[];
  inferred: string[];
}
