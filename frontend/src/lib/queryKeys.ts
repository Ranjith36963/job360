// ---------------------------------------------------------------------------
// TanStack Query key conventions for Job360
//
// Rules:
// 1. All keys are arrays (never strings) for consistent prefix-matching.
// 2. Top-level domain is always the first element.
// 3. Sub-resources follow: ["domain", "sub", ...specifics].
// 4. Invalidate by prefix: queryClient.invalidateQueries({ queryKey: ["jobs"] })
//    clears ALL jobs queries regardless of their filter sub-key.
//
// Key map:
//   ["jobs", "list", filters?]   — paginated/filtered job list (dashboard)
//   ["jobs", "detail", id]       — single job details (job/:id page)
//   ["status"]                   — pipeline run status (last_run, sources)
//   ["pipeline", "applications"] — Kanban application rows
//   ["pipeline", "counts"]       — per-stage counts
//   ["pipeline", "reminders"]    — overdue application reminders
//   ["profile"]                  — current user profile
//   ["profile", "versions"]      — profile version history
//   ["channels"]                 — notification channel list
// ---------------------------------------------------------------------------

import type { JobFilters } from "./types";

export const queryKeys = {
  /** All jobs queries — use as invalidation prefix */
  jobs: (): readonly ["jobs"] => ["jobs"],

  /** Filtered/paginated job list */
  jobList: (filters?: JobFilters): readonly unknown[] =>
    filters ? ["jobs", "list", filters] : ["jobs", "list"],

  /** Single job detail */
  jobDetail: (id: number): readonly unknown[] => ["jobs", "detail", id],

  /** Pipeline status (last run info) */
  status: (): readonly ["status"] => ["status"],

  /** All pipeline queries */
  pipeline: (): readonly ["pipeline"] => ["pipeline"],

  /** Kanban application rows */
  pipelineApplications: (): readonly unknown[] => ["pipeline", "applications"],

  /** Per-stage application counts */
  pipelineCounts: (): readonly unknown[] => ["pipeline", "counts"],

  /** Overdue/reminder applications */
  pipelineReminders: (): readonly unknown[] => ["pipeline", "reminders"],

  /** Current user profile */
  profile: (): readonly ["profile"] => ["profile"],

  /** Profile version history */
  profileVersions: (): readonly unknown[] => ["profile", "versions"],

  /** Notification channel list */
  channels: (): readonly ["channels"] => ["channels"],
};
