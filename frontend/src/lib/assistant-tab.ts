// Pure words behind Profile -> Assistant and Setup. No React, no fetching.
import { formatFeedTime } from "@/lib/home";
import { whoLabel } from "@/lib/event-labels";
import { LINE_SWITCH_LABEL, SUBMIT_MODE_LABEL } from "@/lib/morning-check";

export const LINE_SWITCHES: { value: "ask" | "auto"; label: string }[] = [
  { value: "ask", label: "Ask me" },
  { value: "auto", label: "Apply on its own" },
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
export const CHECK_EVERY = ["1h", "3h", "6h", "12h", "24h"] as const;
/** The backend's ASSISTANT_DAILY_CAP_MAX default (core/settings.py); the server re-checks. */
export const DAILY_CAP_MAX = 500;
export const HARD_STOPS = [
  "Indeed and LinkedIn: your assistant always asks you first.",
  "A login page or a CAPTCHA: your assistant pauses and asks you.",
  "The first application after you turn on sending on its own: a practice run, it stops for you to check.",
];

/** Typed reads of a generated SettingFieldOut (its value/effective are `unknown`). */
export function settingEffective(f: { effective?: unknown; value?: unknown } | undefined): unknown {
  return f?.effective ?? f?.value;
}

/** The score line as a number, or null when unset / not numeric. */
export function settingScore(f: { effective?: unknown; value?: unknown } | undefined): number | null {
  const v = settingEffective(f);
  if (v == null || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/** The automatic-employer-emails switch (off unless the server says true). */
export function settingAutoEmails(f: { effective?: unknown; value?: unknown } | undefined): boolean {
  return settingEffective(f) === true;
}

export const AUTO_EMAILS_HELP =
  "Check-in 7 days after applying, a feedback request after a rejection, one follow-up 7 days later. Sent in your name by your assistant.";

/** The automatic-outreach-emails switch: on only when the server says "auto" (anything else is draft only). */
export function settingOutreachSend(f: { effective?: unknown; value?: unknown } | undefined): boolean {
  return settingEffective(f) === "auto";
}

export const OUTREACH_SEND_HELP =
  "Off: your assistant drafts emails to recruiters and people you contact, and you send them. On: it may send them from your Gmail itself, within the daily and per-company limits.";

/** A line switch, narrowed to "ask" | "auto" (anything else is "ask"). */
export function settingSwitch(f: { effective?: unknown; value?: unknown } | undefined): "ask" | "auto" {
  return settingEffective(f) === "auto" ? "auto" : "ask";
}

export const P_ABOVE = "assistant_settings.above_line";
export const P_BELOW = "assistant_settings.below_line";
export const P_SCORE = "assistant_settings.apply_min_score";
export const P_SUBMIT = "assistant_settings.submit_mode";
export const P_CAP = "assistant_settings.daily_cap";
export const P_COMPANY_CAP = "assistant_settings.company_cap";
export const P_COMPANY_DAYS = "assistant_settings.company_cap_days";
export const P_WEEKLY_CAP = "assistant_settings.weekly_cap";
export const P_MIN_GAP = "assistant_settings.min_gap_minutes";
export const P_HOURS = "assistant_settings.apply_hours";
/** Owner 2026-10-25: free-text "my PC is on" time (max 60). The backend field may not exist yet; the row feature-detects it. */
export const P_PC_ON = "assistant_settings.pc_on_time";
export const PC_ON_MAX = 60;
/** The saved PC-on text from a settings view value (a string, or a field object holding one). */
export function pcOnText(v: unknown): string {
  const raw = v && typeof v === "object" && "value" in v ? (v as { value: unknown }).value : v;
  return typeof raw === "string" ? raw : "";
}
export const P_GRANT_COUNT = "assistant_settings.auto_grant_count";
export const P_GRANT_UNTIL = "assistant_settings.auto_grant_until";
/** Server limits (core/settings.py + assistant_settings.py); the server re-checks every save. */
export const COMPANY_CAP_MAX = 50;
export const COMPANY_DAYS_MAX = 365;
export const WEEKLY_CAP_MAX = 2000;
export const MIN_GAP_MAX = 240;
export const GRANT_COUNT_MAX = 100;
export const GRANT_MAX_DAYS = 7;
export const P_AUTO_EMAILS = "assistant_settings.auto_employer_emails";
export const P_OUTREACH_SEND = "assistant_settings.outreach_send";
export const P_INBOX = "preferences.daily_check";
export const P_EVERY = "preferences.check_every";
/** Owner 2026-10-23: hunt for jobs every N hours (1-168); empty or 0 = off. */
export const P_HUNT_EVERY = "assistant_settings.hunt_every_hours";
export const HUNT_EVERY_MAX = 168;
/** Owner 2026-10-24: may the user's scheduled task find people to reach for new applications. */
export const P_PEOPLE_HUNT = "assistant_settings.people_hunt";
export const PEOPLE_HUNT_HELP =
  "From its own scheduled task, your assistant finds up to 2 people for each new application and writes the messages. You send them yourself.";
/** Paths whose history rides `/assistant-settings/history` (the rest use the profile history). */
export const SETTING_HISTORY_PATHS = [
  P_SCORE, P_ABOVE, P_BELOW, P_SUBMIT, P_CAP, P_AUTO_EMAILS, P_OUTREACH_SEND,
  P_COMPANY_CAP, P_COMPANY_DAYS, P_WEEKLY_CAP, P_MIN_GAP, P_HOURS, P_GRANT_COUNT, P_GRANT_UNTIL, P_HUNT_EVERY,
  P_PEOPLE_HUNT,
] as const;
export const PREF_HISTORY_PATHS = [P_INBOX, P_EVERY] as const;
export const HISTORY_PATHS: string[] = [...SETTING_HISTORY_PATHS, ...PREF_HISTORY_PATHS];

const KEY_LABEL: Record<string, string> = {
  [P_SCORE]: "Score line",
  [P_ABOVE]: "Jobs at or above your line",
  [P_BELOW]: "Jobs below your line",
  [P_SUBMIT]: "Submit mode",
  [P_CAP]: "Daily limit",
  [P_AUTO_EMAILS]: "Automatic employer emails",
  [P_OUTREACH_SEND]: "Automatic outreach emails",
  [P_COMPANY_CAP]: "Applications per company",
  [P_COMPANY_DAYS]: "Days for the company limit",
  [P_WEEKLY_CAP]: "Weekly limit",
  [P_MIN_GAP]: "Wait between applications",
  [P_HOURS]: "Hours to apply",
  [P_GRANT_COUNT]: "Auto for my next applications",
  [P_GRANT_UNTIL]: "Auto lasts until",
  [P_INBOX]: "Gmail check",
  [P_EVERY]: "Inbox check",
  [P_HUNT_EVERY]: "Job hunt",
  [P_PC_ON]: "My PC-on time",
  [P_PEOPLE_HUNT]: "People hunt",
};

/** "auto"/"scheduled" -> auto, "declined" -> paused (Off); anything else as stored. */
export function inboxKey(v: unknown): string {
  return v === "scheduled" ? "auto" : v === "declined" ? "paused" : typeof v === "string" ? v : "";
}

/** "6h" -> "every 6 hours". */
export function everyText(v: unknown): string {
  const n = typeof v === "string" ? /^(\d+)h$/.exec(v)?.[1] : undefined;
  return n === "1" ? "every hour" : n ? `every ${n} hours` : "";
}

/** When the people hunt last ran, in words; "not run yet" when it never has. */
export function peopleHuntLastText(iso: string | null | undefined, now: Date = new Date()): string {
  return iso ? `Last ran ${historyWhen(iso, now)}` : "Not run yet";
}

/** A saved hunt interval in words: 6 -> "every 6 hours"; empty or 0 -> "off". */
export function huntText(v: unknown): string {
  const n = typeof v === "number" ? v : Number.NaN;
  if (!Number.isInteger(n) || n < 1) return "off";
  return n === 1 ? "every hour" : `every ${n} hours`;
}

/** A stored value in words. An empty one is "not set" ("no limit" for the daily limit). */
export function valueText(path: string, v: unknown): string {
  const empty = v === null || v === undefined || v === "";
  if (path === P_AUTO_EMAILS || path === P_PEOPLE_HUNT) return v === true ? "on" : "off";
  if (path === P_OUTREACH_SEND) return v === "auto" ? "on" : "off";
  if (path === P_CAP || path === P_COMPANY_CAP || path === P_WEEKLY_CAP) return empty ? "no limit" : String(v);
  if (path === P_MIN_GAP) return empty || v === 0 ? "no wait" : `${String(v)} minutes`;
  if (path === P_HOURS) {
    const h = v as { from?: unknown; to?: unknown } | null;
    return h && typeof h === "object" && h.from && h.to ? `${String(h.from)} to ${String(h.to)}` : "any time";
  }
  if (path === P_GRANT_UNTIL) return empty ? "not set" : historyWhen(String(v));
  if (path === P_HUNT_EVERY) return huntText(v);
  if (empty) return "not set";
  if (path === P_ABOVE || path === P_BELOW) return LINE_SWITCH_LABEL[String(v)] ?? String(v);
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
  { id: "targets", label: "Job targets" },
  { id: "you", label: "About you" },
  { id: "visa", label: "Right to work" },
  { id: "logistics", label: "Logistics" },
  { id: "equality", label: "Equality (voluntary)" },
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


/** A stored whole number as input text ("" when not set). */
export function numText(f: { effective?: unknown; value?: unknown } | undefined): string {
  const v = f?.value;
  return v == null || v === "" ? "" : String(v);
}

/** The saved apply window as two "HH:MM" strings ("" = not set). */
export function hoursOf(f: { effective?: unknown; value?: unknown } | undefined): { from: string; to: string } {
  const v = f?.value as { from?: unknown; to?: unknown } | null | undefined;
  return v && typeof v === "object" ? { from: String(v.from ?? ""), to: String(v.to ?? "") } : { from: "", to: "" };
}

/** An ISO time as the `datetime-local` input wants it, in the user's own clock ("" if bad). */
export function isoToLocalInput(iso: string): string {
  const d = new Date(iso);
  if (!iso || Number.isNaN(d.getTime())) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** A `datetime-local` value (the user's clock) as an ISO time with an offset ("" if bad). */
export function localInputToIso(local: string): string {
  const d = new Date(local);
  return local && !Number.isNaN(d.getTime()) ? d.toISOString() : "";
}

/** Is the grant still running? (a count and a future end). */
export function grantActive(count: unknown, until: unknown, now: Date = new Date()): boolean {
  if (count == null || count === "" || typeof until !== "string" || !until) return false;
  const t = new Date(until).getTime();
  return !Number.isNaN(t) && t > now.getTime();
}

/** Plain words for the grant. */
export function grantText(count: unknown, until: string): string {
  return `Auto for my next ${String(count)} applications until ${historyWhen(until)}, then back to asking`;
}

/** One side of the settings' `auto_grant_scope` (gap roadmap slice 30). */
export type GrantScopeCount = { count: number; hosts: string[]; no_host: number };

/** What a grant covers, in plain words: "2 applications (sites: a.com, b.com)" - "" for none.
 *  An application with no site yet is named too: the grant can never send it on its own. */
export function scopeText(scope: GrantScopeCount | null | undefined): string {
  if (!scope || scope.count <= 0) return "";
  const apps = `${scope.count} application${scope.count === 1 ? "" : "s"}`;
  const sites = scope.hosts.length ? ` (sites: ${scope.hosts.join(", ")})` : "";
  const none = scope.no_host > 0 ? `, ${scope.no_host} with no site yet` : "";
  return `${apps}${sites}${none}`;
}
