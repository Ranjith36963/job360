// ---------------------------------------------------------------------------
// TanStack Query key conventions for Job360
//
// Rules:
// 1. All keys are arrays (never strings) for consistent prefix-matching.
// 2. Top-level domain is always the first element.
// 3. Sub-resources follow: ["domain", "sub", ...specifics].
//
// Key map:
//   ["pipeline", "applications"] — Kanban application rows
//   ["pipeline", "counts"]       — per-stage counts
//   ["pipeline", "reminders"]    — overdue application reminders
//   ["profile"]                  — current user profile
//   ["profile", "versions"]      — profile version history
//   ["channels"]                 — notification channel list
// ---------------------------------------------------------------------------

export const queryKeys = {
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
