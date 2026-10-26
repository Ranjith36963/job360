/**
 * Browser wording shown on the web. The three BROWSER_LABELS equal the server's
 * `backend/src/services/browser_agent/routing.py` LABELS, so a chip and a heading can never drift
 * (a test pins both). Assistant-neutral on purpose (owner, 2026-10-25): no assistant or browser
 * product is named in any string here.
 */
export const BROWSER_LABELS = {
  job360: "I'll use Job360's browser",
  own_browser: "Needs your own browser: turn your PC on",
  human: "Only you can do this",
} as const;

/** The chip for linkedin.com (owner, 2026-10-26): the user's own browser by their choice, nothing failed.
 *  Equals the server's `routing.LINKEDIN_LABEL` (a test pins both). */
export const LINKEDIN_OWN_BROWSER_LABEL = "Your own browser: runs when your PC is on";
/** The route reason the server sends with it. */
export const LINKEDIN_OWN_BROWSER_REASON = "linkedin_own_browser";

/** The section heading for tasks that wait for the user's own browser. */
export const OWN_BROWSER_LABEL = BROWSER_LABELS.own_browser;
/** The short name of Job360's own browser, on a chip with no server label. */
export const JOB360_BROWSER_LABEL = "Job360's browser";
/** The pace line on a job card. */
export const PACE_TEXT =
  "LinkedIn sends go out from your own browser only, when your PC is on. 2–3 minutes between sends, at most 10 LinkedIn sends a day.";

export const RUN_OWN_BROWSER_TASKS = "Run my own-browser tasks now";
export const RUN_NOW_DONE = "Open for the next 60 minutes. Your assistant on your PC takes them one at a time.";

/** The server's route reason codes (`routing.ROUTE_REASONS`) in plain words. "default" and the
 *  LinkedIn choice need no line. A code not listed here is never shown raw. */
export const REASON_WORDS: Record<string, string> = {
  job360_login_required: "Job360's browser reached a sign-in page it cannot pass.",
  job360_bot_check: "Job360's browser was stopped by a robot check.",
  job360_safety_stop: "Job360's browser stopped here to be safe.",
  password: "It asks for your password. Only you type passwords.",
  mfa: "It asks for a one-time code. Only you can enter it.",
  only_you: "This step is yours: no browser does it for you.",
};

/** The words for a route reason: the plain line for a known code, "" for an unknown code
 *  (a bare snake_case word), and an older server's already-written sentence as it is. */
export function reasonWords(reason: string): string {
  if (reason in REASON_WORDS) return REASON_WORDS[reason];
  return /^[a-z0-9_]+$/.test(reason) ? "" : reason;
}
