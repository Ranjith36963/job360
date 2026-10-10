// Pure words behind Profile -> Assistant and Setup. No React, no fetching.
import { formatFeedTime } from "@/lib/home";
import { whoLabel } from "@/lib/event-labels";
import { APPLY_MODE_LABEL, SUBMIT_MODE_LABEL } from "@/lib/morning-check";

export const APPLY_MODES: { value: string; sub: string }[] = [
  { value: "ask_each", sub: "Before any work, it shows you the fit and asks: apply or skip?" },
  { value: "apply_all", sub: "It applies to every job it brings, under your other settings." },
  {
    value: "selective_above_score",
    sub: "It applies on its own when its fit score is at or above the line; below, it asks.",
  },
];
export const SUBMIT_MODES: { value: string; sub: string }[] = [
  { value: "confirm", sub: "It stops at Submit and shows you Ready to send." },
  {
    value: "auto_when_sure",
    sub: "It clicks Submit only for a CV you have seen, after one practice run. Indeed and LinkedIn still ask.",
  },
];
export const INBOX_MODES: { value: string; label: string }[] = [
  { value: "auto", label: "Auto" },
  { value: "ask", label: "Ask first" },
  { value: "paused", label: "Off" },
];
export const CHECK_EVERY = ["3h", "6h", "12h", "24h"] as const;
/** The backend's ASSISTANT_DAILY_CAP_MAX default (core/settings.py); the server re-checks. */
export const DAILY_CAP_MAX = 500;
export const HARD_STOPS = [
  "Indeed and LinkedIn: your assistant always asks you first.",
  "A login page or a CAPTCHA: your assistant pauses and asks you.",
  "The first application after you turn on sending on its own: a practice run, it stops for you to check.",
];

export const P_APPLY = "assistant_settings.apply_mode";
export const P_SCORE = "assistant_settings.apply_min_score";
export const P_SUBMIT = "assistant_settings.submit_mode";
export const P_CAP = "assistant_settings.daily_cap";
export const P_INBOX = "preferences.daily_check";
export const P_EVERY = "preferences.check_every";
/** Paths whose history rides `/assistant-settings/history` (the rest use the profile history). */
export const SETTING_HISTORY_PATHS = [P_APPLY, P_SCORE, P_SUBMIT, P_CAP] as const;
export const PREF_HISTORY_PATHS = [P_INBOX, P_EVERY] as const;
export const HISTORY_PATHS: string[] = [...SETTING_HISTORY_PATHS, ...PREF_HISTORY_PATHS];

const KEY_LABEL: Record<string, string> = {
  [P_APPLY]: "Apply mode",
  [P_SCORE]: "Score line",
  [P_SUBMIT]: "Submit mode",
  [P_CAP]: "Daily limit",
  [P_INBOX]: "Gmail check",
  [P_EVERY]: "Inbox check",
};

/** "auto"/"scheduled" -> auto, "declined" -> paused (Off); anything else as stored. */
export function inboxKey(v: unknown): string {
  return v === "scheduled" ? "auto" : v === "declined" ? "paused" : typeof v === "string" ? v : "";
}

/** "6h" -> "every 6 hours". */
export function everyText(v: unknown): string {
  const n = typeof v === "string" ? /^(\d+)h$/.exec(v)?.[1] : undefined;
  return n ? `every ${n} hours` : "";
}

/** A stored value in words. An empty one is "not set" ("no limit" for the daily limit). */
export function valueText(path: string, v: unknown): string {
  const empty = v === null || v === undefined || v === "";
  if (path === P_CAP) return empty ? "no limit" : String(v);
  if (empty) return "not set";
  if (path === P_APPLY) return APPLY_MODE_LABEL[String(v)] ?? String(v);
  if (path === P_SUBMIT) return SUBMIT_MODE_LABEL[String(v)] ?? String(v);
  if (path === P_INBOX) return INBOX_MODES.find((m) => m.value === inboxKey(v))?.label ?? String(v);
  if (path === P_EVERY) return everyText(v) || String(v);
  return String(v);
}

export type HistRow = { set_by: string; set_at: string; value?: unknown };
export type ChangeRow = {
  key: string;
  path: string;
  label: string;
  by: string;
  at: string;
  was: string;
  now: string;
  /** Only the newest change of a setting can be taken back. */
  newest: boolean;
  /** What "Take back" writes: the value before this change (null = nothing). */
  previous: unknown;
};

/** "Today 09:14" for today, else "Sun 21:02". */
export function historyWhen(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const t = formatFeedTime(iso, now);
  return d.toDateString() === now.toDateString() ? `Today ${t}` : t;
}

/** One list from every setting's history (each newest first), newest change first. */
export function mergeHistory(byPath: Record<string, HistRow[]>): ChangeRow[] {
  const out: ChangeRow[] = [];
  for (const [path, rows] of Object.entries(byPath)) {
    rows.forEach((r, i) => {
      const prev = rows[i + 1]?.value ?? null;
      out.push({
        key: `${path}-${r.set_at}-${i}`,
        path,
        label: KEY_LABEL[path] ?? path,
        by: whoLabel(r.set_by).name,
        at: r.set_at,
        was: valueText(path, prev),
        now: valueText(path, r.value),
        newest: i === 0,
        previous: prev,
      });
    });
  }
  return out.sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
}

// ---- Setup (the six rounds) ----
export const ROUNDS: { id: string; label: string }[] = [
  { id: "you", label: "About you" },
  { id: "visa", label: "Right to work" },
  { id: "logistics", label: "Logistics" },
  { id: "equality", label: "Equality (voluntary)" },
  { id: "targets", label: "Job targets" },
  { id: "settings", label: "Assistant settings" },
];
export const RESUME_LINE = "Ask your assistant: run 360";

export function roundLabel(id: string): string {
  return ROUNDS.find((r) => r.id === id)?.label ?? id;
}

/** "4 Oct" (same style as the Memory tab's "Saved by Claude, 3 Oct"); "" for a bad date. */
export function doneDate(iso: string | undefined): string {
  const d = new Date(iso ?? "");
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}
