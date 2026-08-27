/**
 * safeNext — the open-redirect guard for post-login redirects.
 *
 * ONE definition, shared by every sign-in path (wiring.md W-01). It used to live
 * inside `(auth)/login/page.tsx` where only the password form could reach it, so
 * the magic-link form — the DEFAULT path — always dropped the user on /dashboard.
 * When our own digest email linked someone to /jobs/123 and their session had
 * expired, the job was gone by the time they signed in.
 *
 * This is defence in depth, not the only check: the backend re-validates with the
 * same rules in `services/auth/magic_link.safe_next_path`, because the emailed link
 * is built server-side and a browser-only check is bypassed by calling the API
 * directly.
 */

/** Where we send people when there is no trustworthy destination. */
export const DEFAULT_NEXT = "/dashboard";

/** Long enough for any real route + query string; short enough to bound abuse. */
const MAX_NEXT_LENGTH = 512;

/**
 * Return `p` if it is a safe same-site path, otherwise {@link DEFAULT_NEXT}.
 *
 * Rejected, and why each one matters:
 * - not starting with `/` — an absolute URL or bare scheme (`https://evil.com`,
 *   `javascript:`) leaves the site outright.
 * - `//evil.com` — protocol-relative: the browser supplies the scheme and reads
 *   the rest as a HOST, so it is off-site despite the leading slash.
 * - any backslash — the WHATWG URL parser folds `\` into `/`, so `/\evil.com`
 *   becomes `//evil.com` in the browser while sailing past a naive `startsWith("//")`
 *   test. This is the hole the original version had.
 * - control characters (CR, LF, NUL, tab) — injection into the URL, and into the
 *   sign-in email the backend builds from this value.
 * - anything over {@link MAX_NEXT_LENGTH}.
 *
 * Never throws: a hostile value degrades to the dashboard rather than blocking
 * sign-in.
 */
export function safeNext(p: string | null | undefined): string {
  if (!p || typeof p !== "string") return DEFAULT_NEXT;
  if (p.length > MAX_NEXT_LENGTH) return DEFAULT_NEXT;
  // Control chars anywhere: CR/LF/NUL/tab. They matter doubly here because the
  // backend interpolates this value into the sign-in email it sends.
  if (/[\u0000-\u001f\u007f]/.test(p)) return DEFAULT_NEXT;
  if (p.includes("\\")) return DEFAULT_NEXT;
  if (!p.startsWith("/") || p.startsWith("//")) return DEFAULT_NEXT;
  return p;
}
