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

/** One chip per route reason, equal to the server's `routing.ROUTE_LABELS` (a test pins both). `{site}`,
 *  `{date}`, `{until}`, `{n}` and `{cap}` are filled from closed values. Assistant-neutral. */
export const ROUTE_LABELS = {
  default: "I'll use Job360's browser",
  linkedin_own_browser: "Your own browser: runs when your PC is on",
  learned_blocked: "Job360's browser was blocked on {site} on {date}: your own browser until {until}",
  learned_login: "{site} asks you to sign in: your own browser, or sign in once in Job360's browser",
  probe: "Trying Job360's browser on {site} again",
  at_cap: "Today's LinkedIn limit is used ({n} of {cap}): waits until tomorrow",
  linkedin_paused: "LinkedIn showed a limit warning: LinkedIn tasks paused until {date}",
  account_step: "This site wants a new account: your own browser, or sign in once in Job360's browser",
  linkedin_guest_blocked: "LinkedIn blocked a signed-out read: LinkedIn job pages wait until {date}",
  waiting: "Waiting for your PC",
} as const;

type RouteFmt = Partial<Record<"site" | "date" | "until" | "n" | "cap", string | number>>;

/** Fill a `ROUTE_LABELS` string from closed values; a missing value reads as a neutral default. */
export function routeLabel(reason: keyof typeof ROUTE_LABELS, fmt: RouteFmt = {}): string {
  const defaults = { site: "this site", date: "soon", until: "soon", n: "?", cap: "?" };
  return ROUTE_LABELS[reason].replace(/\{(site|date|until|n|cap)\}/g, (_, k: keyof typeof defaults) =>
    String(fmt[k] ?? defaults[k]),
  );
}

/** The section heading for tasks that wait for the user's own browser. */
export const OWN_BROWSER_LABEL = BROWSER_LABELS.own_browser;
/** The short name of Job360's own browser, on a chip with no server label. */
export const JOB360_BROWSER_LABEL = "Job360's browser";
/** The pace line on a job card. */
export const PACE_TEXT =
  "LinkedIn sends go out from your own browser only, when your PC is on. 2–3 minutes between sends, at most 10 LinkedIn sends a day.";

export const RUN_OWN_BROWSER_TASKS = "Run my own-browser tasks now";
export const RUN_NOW_DONE = "Open for the next 60 minutes. Your assistant on your PC takes them one at a time.";
