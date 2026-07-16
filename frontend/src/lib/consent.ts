/**
 * Analytics consent (docs/fable/05 C3).
 *
 * Why this exists: PostHog was initialising on page load, before the user agreed
 * to anything. Under UK GDPR + PECR, analytics/tracking cookies need consent
 * BEFORE they are set — "reject" must be as easy as "accept", and silence is not
 * consent. So nothing analytics-related runs until `getConsent() === "accepted"`.
 *
 * Stored in localStorage rather than a cookie on purpose: the choice itself is
 * not a tracking identifier, it never needs to reach the server, and keeping it
 * out of the cookie jar means a declined visitor sends no analytics state at all.
 */

export const CONSENT_KEY = "job360_analytics_consent";
/** Fired on the window when the choice changes, so listeners react immediately
 *  instead of waiting for a reload. */
export const CONSENT_EVENT = "job360:consent-changed";

export type ConsentValue = "accepted" | "declined";

/** The stored choice, or null when the user has not decided yet (→ show banner,
 *  and treat as NO consent). SSR-safe: returns null on the server. */
export function getConsent(): ConsentValue | null {
  if (typeof window === "undefined") return null;
  try {
    const v = window.localStorage.getItem(CONSENT_KEY);
    return v === "accepted" || v === "declined" ? v : null;
  } catch {
    // Private mode / storage blocked → treat as "not consented".
    return null;
  }
}

/** Subscribe to consent changes — the `subscribe` half of the
 *  useSyncExternalStore contract (getConsent is the snapshot half). */
export function subscribeConsent(onChange: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(CONSENT_EVENT, onChange);
  return () => window.removeEventListener(CONSENT_EVENT, onChange);
}

export function setConsent(value: ConsentValue): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(CONSENT_KEY, value);
  } catch {
    // Storage blocked — still notify listeners for this page's lifetime.
  }
  window.dispatchEvent(new CustomEvent(CONSENT_EVENT, { detail: value }));
}
