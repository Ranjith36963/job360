// Pure words behind the Needs-you "Morning check". No React, no fetching.
import { formatFeedTime } from "@/lib/home";

/** The morning check owns its stamp: Home rewrites `job360-last-visit` on every
 * view, which would zero this tally the moment the user opened Home. */
export const LAST_CHECK_KEY = "job360-last-check";
/** A first visit (no stamp) looks back this far. */
export const CHECK_LOOKBACK_DAYS = 1;

/** The two per-user switches (above / below the score line). */
export const LINE_SWITCH_LABEL: Record<string, string> = {
  ask: "Ask me",
  auto: "Apply on its own",
  skip: "Drop it",
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

/** The strip's score-line words. null / undefined = never set. */
export type LineSwitch = "ask" | "auto";

/** Narrow a backend string to a known switch; anything else reads as "ask". */
export function asLineSwitch(v: unknown): LineSwitch {
  return v === "auto" ? "auto" : "ask";
}

export function scoreLineText(line: number | null | undefined): string {
  return line == null ? "Score line not set" : `Score line ${line}`;
}

/** "Above: ask me · Below: ask me" for the strip. */
export function lineSwitchText(above: string | undefined, below: string | undefined): string {
  const w = (v: string | undefined) => (LINE_SWITCH_LABEL[v ?? "ask"] ?? "Ask me").toLowerCase();
  return `Above: ${w(above)} · Below: ${w(below)}`;
}

// ---- Batch auto apply (owner decision 2026-10-19) ----
// One switch replaces "above / below / submit mode": ON = apply on its own to every
// job at or above the score line (above_line auto + submit_mode auto_when_sure); OFF =
// ask before each one (ask + confirm). Jobs below the line are dropped (below_line skip).

/** Always shown, in amber, under the score line. */
export const ALWAYS_ASKS =
  "Your assistant always stops and asks you when it is not sure: a CAPTCHA, a login page, a guessed answer, or anything not verified.";

/** ON only when BOTH halves are set; anything in between reads as off (it still asks). */
export function batchOn(above: string | null | undefined, submit: string | null | undefined): boolean {
  return above === "auto" && submit === "auto_when_sure";
}

export function batchText(on: boolean): string {
  return on ? "Batch auto apply: on" : "Batch auto apply: off";
}

/** What happens to a job below the line, in words. */
export function belowText(below: string | null | undefined): string {
  if (below === "skip") return "Below the line: dropped";
  return below === "auto" ? "Below the line: applies on its own" : "Below the line: asks you";
}

/** The PATCH edits to reach (score, batch) from what the server holds. Saving always
 *  records "drop jobs below the line" - the user's own click, so never a default. */
export function batchEdits(
  state: { apply_min_score?: number | null; above_line?: string | null; below_line?: string | null; submit_mode?: string | null },
  want: { scoreValue: number | null; batch: boolean },
): { path: string; value: unknown }[] {
  const out: { path: string; value: unknown }[] = [];
  if (want.scoreValue !== (state.apply_min_score ?? null)) {
    out.push({ path: "assistant_settings.apply_min_score", value: want.scoreValue });
  }
  const above = want.batch ? "auto" : "ask";
  const submit = want.batch ? "auto_when_sure" : "confirm";
  if ((state.above_line || "ask") !== above) out.push({ path: "assistant_settings.above_line", value: above });
  if ((state.submit_mode || "confirm") !== submit) out.push({ path: "assistant_settings.submit_mode", value: submit });
  if (state.below_line !== "skip") out.push({ path: "assistant_settings.below_line", value: "skip" });
  return out;
}
