// ---------------------------------------------------------------------------
// Pure logic behind the signed-in Home page. No React, no fetching — so the
// sentence, the last-visit stamp and the feed are all unit-testable.
// Every string that comes from the API (names, companies, details) stays a
// plain string here; the components render it as a React text node only.
// ---------------------------------------------------------------------------

import { eventTitle } from "@/lib/event-detail";
import { whoLabel } from "@/lib/event-labels";
import type { WhatsNewEvent, WhatsNewResponse } from "@/lib/api";

type WhatsNewApplication = WhatsNewResponse["applications"][number];

/** The one localStorage key Home owns. It holds ONE ISO timestamp, nothing else. */
export const LAST_VISIT_KEY = "job360-last-visit";

const DAY_MS = 24 * 60 * 60 * 1000;

/** How far back the first visit (no stored stamp) looks. */
export const DEFAULT_LOOKBACK_DAYS = 7;
/** How far back "What your assistant did" looks. */
export const FEED_LOOKBACK_DAYS = 30;
/** Rows shown in "What your assistant did". */
export const FEED_LIMIT = 12;
/** Most pages of `whats-new` Home will follow (200 events each). */
export const MAX_WHATS_NEW_PAGES = 3;
/** The feed needs the NEWEST events but the route pages oldest first, so it
 * reads further (up to 5,000 events in the window) before giving up. */
export const FEED_MAX_PAGES = 25;
/** Open asks shown as cards on Home before "See all". */
export const HOME_ASKS_LIMIT = 3;

// ---- Last visit (per browser) --------------------------------------------

/** The ISO stamp under `key` (Home and the Needs-you morning check own different
 * keys). Absent, unreadable or unparseable → `lookbackDays` before `now`. Never throws. */
export function readLastVisit(
  now: Date = new Date(),
  key: string = LAST_VISIT_KEY,
  lookbackDays: number = DEFAULT_LOOKBACK_DAYS,
): string {
  const fallback = new Date(now.getTime() - lookbackDays * DAY_MS).toISOString();
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return fallback;
    return Number.isNaN(new Date(raw).getTime()) ? fallback : raw;
  } catch {
    return fallback;
  }
}

/** True when a readable stamp is stored under `key` (false on a first visit). */
export function hasStamp(key: string): boolean {
  try {
    return !Number.isNaN(new Date(window.localStorage.getItem(key) ?? "").getTime());
  } catch {
    return false;
  }
}

/** Remember `iso` under `key`. A blocked or full store is silently ignored. */
export function writeLastVisit(iso: string, key: string = LAST_VISIT_KEY): void {
  try {
    window.localStorage.setItem(key, iso);
  } catch {
    // Private window / blocked storage — the page still works, it just won't remember.
  }
}

/** `n` days before `now`, as an ISO string. */
export function daysAgoIso(n: number, now: Date = new Date()): string {
  return new Date(now.getTime() - n * DAY_MS).toISOString();
}

// ---- Events ---------------------------------------------------------------

/** Events written by an assistant (anything that is not the user in the browser). */
export function assistantEvents<T extends { recorded_by: string }>(events: readonly T[]): T[] {
  return events.filter((e) => e.recorded_by !== "web");
}

/** The distinct assistant names behind a set of events, in first-seen order. */
export function assistantNames(events: readonly { recorded_by: string }[]): string[] {
  const names: string[] = [];
  for (const e of assistantEvents(events)) {
    const name = whoLabel(e.recorded_by).name;
    if (!names.includes(name)) names.push(name);
  }
  return names;
}

// ---- The top sentence -----------------------------------------------------

/** One run of the sentence. `em` marks the single highlighted phrase. */
export type SentencePart = { text: string; em?: boolean };

const WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"];

function capitalise(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** "One thing needs you." / "Two things need you." — up to ten in words, then digits. */
export function needsYouSentence(k: number): string {
  if (k <= 0) return "";
  if (k === 1) return "One thing needs you.";
  const word = k <= 10 ? WORDS[k] : String(k);
  return `${capitalise(word)} things need you.`;
}

/** "Claude" / "Claude and Codex" / "Claude, Codex and Grok" — every name, as recorded. */
export function joinNames(names: readonly string[]): string {
  if (names.length <= 1) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/**
 * The owner-approved opening line of Home.
 *
 * `events` are the events since the last visit; only those NOT written by the
 * user in the browser count. `truncated` means there were more than we paged
 * through, so the count shows as "N+". `openAsks` is how many questions wait.
 */
export function buildSentence(args: {
  events: readonly { recorded_by: string }[];
  truncated: boolean;
  openAsks: number;
}): SentencePart[] {
  const mine = assistantEvents(args.events);
  const n = mine.length;
  const parts: SentencePart[] = [];

  if (n === 0) {
    parts.push({ text: "Nothing new since you were last here." });
  } else {
    const names = assistantNames(mine);
    const count = `${n}${args.truncated ? "+" : ""}`;
    const noun = n === 1 && !args.truncated ? "record" : "records";
    // Owner decision (2026-10-04): name every assistant exactly as it signed
    // its records. 2026-10-14: say plainly it is the user's own assistant and
    // keep the token name in brackets — "Your assistant (lc) wrote ...".
    const who = names.length === 1 ? `Your assistant (${names[0]})` : `Your assistants (${joinNames(names)})`;
    parts.push({ text: `${who} wrote ` });
    parts.push({ text: `${count} ${noun}`, em: true });
    parts.push({ text: " since you were last here." });
  }

  const asks = needsYouSentence(args.openAsks);
  if (asks) parts.push({ text: ` ${asks}` });
  return parts;
}

/** The sentence as one plain string (tests, aria-labels). */
export function sentenceText(parts: readonly SentencePart[]): string {
  return parts.map((p) => p.text).join("");
}

// ---- Brand-new user -------------------------------------------------------

/** A user with no applications, no events and no open question gets the
 * connect steps instead of an empty ledger. A general ask (no application)
 * writes no event, so it is counted on its own — a waiting question must never
 * be hidden behind the welcome. Unknown (a call failed → null) is never
 * "brand new". */
export function isBrandNew(args: {
  applicationsTotal: number | null;
  eventCount: number | null;
  openAsks: number | null;
}): boolean {
  return args.applicationsTotal === 0 && args.eventCount === 0 && args.openAsks === 0;
}

// ---- "What your assistant did" feed ---------------------------------------

export type FeedItem = {
  id: number;
  recordedAt: string;
  company: string;
  assistant: string;
  line: string;
  applicationId: number;
};

/** The label + detail line for one event. Reuses event-labels wording and
 * never invents a claim — an unknown type falls back to its raw name. */
export function feedLine(e: { event_type: string; detail: string; payload?: unknown }): string {
  const label = eventTitle(e);
  const detail = (e.detail ?? "").trim();
  return detail ? `${label}: ${detail}` : label;
}

/** The last `limit` assistant events, newest first. `events` arrive oldest first. */
export function selectFeed(
  events: readonly WhatsNewEvent[],
  applications: readonly WhatsNewApplication[],
  limit: number = FEED_LIMIT
): FeedItem[] {
  const company = new Map(applications.map((a) => [a.id, a.job_company || a.job_title || ""]));
  return assistantEvents(events)
    .slice(-limit)
    .reverse()
    .map((e) => ({
      id: e.id,
      recordedAt: e.recorded_at,
      company: company.get(e.application_id) ?? "",
      assistant: whoLabel(e.recorded_by).name,
      line: feedLine(e),
      applicationId: e.application_id,
    }));
}

/** "09:12" for today, "Fri 18:31" for earlier days — the viewer's own locale. */
export function formatFeedTime(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const time = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return time;
  const day = d.toLocaleDateString(undefined, { weekday: "short" });
  return `${day} ${time}`;
}

/** "Sunday 4 October 2026" in the viewer's own locale. */
export function formatToday(now: Date = new Date()): string {
  return now.toLocaleDateString(undefined, {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

// ---- The last confirmed application (from redesign #745) -------------------

/** The newest `applied` event in `events` and its company, or null. `occurred_at`
 * is the agent's own text, so an unparseable one is skipped, never guessed. */
export function lastApplied(
  events: readonly WhatsNewEvent[],
  applications: readonly WhatsNewApplication[]
): { company: string; occurredAt: string } | null {
  let best: WhatsNewEvent | null = null;
  for (const e of events) {
    if (e.event_type !== "applied" || Number.isNaN(Date.parse(e.occurred_at))) continue;
    if (!best || Date.parse(e.occurred_at) > Date.parse(best.occurred_at)) best = e;
  }
  if (!best) return null;
  const app = applications.find((a) => a.id === best.application_id);
  const company = (app?.job_company || app?.job_title || "").trim();
  return company ? { company, occurredAt: best.occurred_at } : null;
}

/** "today at 14:05", "yesterday at 09:30", "on Tuesday at 11:00" or "on 3 Oct".
 * A bare date has no time to show (JS would read it as UTC midnight), so it
 * gets the day only. Unparseable → "". */
export function dayAt(iso: string, now: Date = new Date()): string {
  const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(iso.trim());
  const d = dateOnly ? new Date(`${iso.trim()}T12:00:00`) : new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  let day: string;
  if (d.toDateString() === now.toDateString()) day = "today";
  else if (d.toDateString() === yesterday.toDateString()) day = "yesterday";
  else if (d < now && now.getTime() - d.getTime() < 6 * DAY_MS)
    day = `on ${d.toLocaleDateString(undefined, { weekday: "long" })}`;
  else day = `on ${d.toLocaleDateString(undefined, { day: "numeric", month: "short" })}`;
  if (dateOnly) return day;
  return `${day} at ${d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}`;
}
