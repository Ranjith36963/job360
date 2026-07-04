// ---------------------------------------------------------------------------
// Job360 Frontend — TypeScript types
//
// Generated aliases: 28 names aliased from the backend OpenAPI schema
// (TailoredDocOut / TailorBundle narrow doc_kind/status to literal unions).
// Frontend-only: JobFilters, DuplicateJobsResponse, DuplicateJobSummary,
//                ProfileVersionDiff, PreferencesRequest, SkillProvenance,
//                SkillTiers, TailorDocKind
// ---------------------------------------------------------------------------

import type { components } from "./api-types";

type Schemas = components["schemas"];

// ---- Generated aliases ----

export type ActionRequest = Schemas["ActionRequest"];
export type ActionResponse = Schemas["ActionResponse"];
export type ApplicationTimelineResponse = Schemas["ApplicationTimelineResponse"];
export type CVDetail = Schemas["CVDetail"];
export type HealthResponse = Schemas["HealthResponse"];
export type JobListResponse = Schemas["JobListResponse"];
export type JobResponse = Schemas["JobResponse"];
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
export type ProfileVersionSummary = Schemas["ProfileVersionSummary"];
export type RunEntry = Schemas["RunEntry"];
export type SearchStartResponse = Schemas["SearchStartResponse"];
export type SearchStatusResponse = Schemas["SearchStatusResponse"];
export type SourceInfo = Schemas["SourceInfo"];
export type StatusResponse = Schemas["StatusResponse"];
export type TimelineEntry = Schemas["TimelineEntry"];

// `doc_kind`/`status` are narrowed from `string` to literal unions — the
// backend OpenAPI declares them as string but only these values are valid
// (docs/peruser_cv_coverletter.md). Same pattern as `Channel` in api.ts.
export type TailorDocKind = "cv" | "cover_letter";

export type TailoredDocOut = Omit<Schemas["TailoredDocOut"], "doc_kind" | "status"> & {
  doc_kind: TailorDocKind;
  status: "draft" | "kept";
};

export type TailorBundle = Omit<Schemas["TailorBundle"], "documents"> & {
  documents: TailoredDocOut[];
};

// ---- Name bridge: frontend name differs from schema name ----

// The backend schema is RunsListResponse (includes limit + offset pagination).
// The old hand-written RecentRunsResponse only had { runs, total }.
// Callers in api.ts and dashboard only use .runs — the extra fields are benign.
export type RecentRunsResponse = Schemas["RunsListResponse"];

// ---- Frontend-only types (NOT in the backend schema) ----

// T4: 10 new filter fields added
export interface JobFilters {
  hours?: number;
  min_score?: number;
  source?: string;
  bucket?: string;
  action?: string;
  visa_only?: boolean;
  visa_sponsorship?: boolean;
  limit?: number;
  offset?: number;
  mode?: string;
  // Step-2 T4 — new filter fields
  seniority?: string;
  employment_type?: string;
  workplace_type?: string;
  salary_min?: number;
  salary_max?: number;
  required_skills?: string[];
  title_canonical?: string;
  industry?: string;
  posted_after?: string;
  posted_before?: string;
  staleness_state?: string;
  sort_by?: "score" | "date" | "salary" | "staleness";
}

// ---- Step-3: Duplicate jobs (C-05) — not in backend OpenAPI schema ----

export interface DuplicateJobSummary {
  id: number;
  title: string;
  company: string;
  source: string;
  match_score: number;
  apply_url: string;
}

export interface DuplicateJobsResponse {
  job_id: number;
  duplicates: DuplicateJobSummary[];
  total: number;
}

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
