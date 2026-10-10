// Pure words behind the Needs-you "Morning check". No React, no fetching.
import { formatFeedTime } from "@/lib/home";

/** The morning check owns its stamp: Home rewrites `job360-last-visit` on every
 * view, which would zero this tally the moment the user opened Home. */
export const LAST_CHECK_KEY = "job360-last-check";
/** A first visit (no stamp) looks back this far. */
export const CHECK_LOOKBACK_DAYS = 1;

export const APPLY_MODE_LABEL: Record<string, string> = {
  ask_each: "Ask about each job",
  apply_all: "Apply to all",
  selective_above_score: "Only above my score line",
};
export const SUBMIT_MODE_LABEL: Record<string, string> = {
  confirm: "Confirm before sending",
  auto_when_sure: "Send on its own when sure",
};

export const BUCKETS = ["sent", "blocked", "waiting", "failed"] as const;
export type Bucket = (typeof BUCKETS)[number];
export const BUCKET_LABEL: Record<Bucket, string> = {
  sent: "Sent",
  blocked: "Blocked",
  waiting: "Waiting",
  failed: "Failed",
};

/** "4 of 10 today" with a daily limit, "4 today" without one. */
export function quotaText(appliedToday: number, dailyCap: number | null | undefined): string {
  return dailyCap == null ? `${appliedToday} today` : `${appliedToday} of ${dailyCap} today`;
}

/** "Since your last visit, Thu 18:40"; `null` (first visit) → "In the last 24 hours". */
export function sinceText(lastCheck: string | null, now: Date = new Date()): string {
  return lastCheck ? `Since your last visit, ${formatFeedTime(lastCheck, now)}` : "In the last 24 hours";
}

/** "Paused by you, 09:40" / "Paused by Claude, 09:40". `web` is the user. */
export function pausedByText(by: string, at: string, now: Date = new Date()): string {
  const name = by === "web" ? "you" : by.replace(/^(token|agent):/, "");
  const when = formatFeedTime(at, now);
  return when ? `Paused by ${name}, ${when}` : `Paused by ${name}`;
}
