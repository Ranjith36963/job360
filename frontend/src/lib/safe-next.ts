// ---------------------------------------------------------------------------
// safeNext — shared open-redirect guard for the ?next query param.
//
// Extracted from src/app/(auth)/login/page.tsx (spec R9: the magic-link
// landing page needs the same check, and a third caller — the magic-link
// request body — should not duplicate it again).
// ---------------------------------------------------------------------------

/**
 * True when `p` is a same-origin path: starts with "/" but not "//", and
 * contains no backslash, tab, CR or LF. The WHATWG URL parser (what the
 * browser and Next's router use) treats "\" as "/" for http(s) and strips
 * tab/CR/LF BEFORE parsing, so "/\evil.com" and "/\t/evil.com" both resolve
 * to https://evil.com/ — a leading-slash check alone is not enough.
 */
export function isSafePath(p: string | null | undefined): p is string {
  return !!p && p.startsWith("/") && !p.startsWith("//") && !/[\\\t\n\r]/.test(p);
}

/**
 * Validates the ?next param to prevent open-redirect attacks. Only allows
 * paths that start with "/" but not "//" (protocol-relative); anything else
 * (external URL, missing, malformed) falls back to /applications (the signed-in home).
 */
export function safeNext(p: string | null | undefined): string {
  return isSafePath(p) ? p : "/applications";
}

/**
 * Same safety check as `safeNext`, but returns `undefined` instead of a
 * fallback — for callers that want to OMIT an unsafe/missing `next` rather
 * than substitute a default (e.g. the magic-link request body: no `next`
 * means "no preference", not "go to /applications").
 */
export function safeNextOrUndefined(p: string | null | undefined): string | undefined {
  return isSafePath(p) ? p : undefined;
}

// ---------------------------------------------------------------------------
// OAuth-consent return cookie. The middleware sets `j360_next` when it bounces
// an unauthenticated /oauth/consent/... request to /login. If the user then
// reaches /login WITHOUT ?next (e.g. the header "Log in" link) the emailed
// magic link carries no `next`; this cookie lets sign-in still land back on
// the consent screen. Scope is deliberately tiny: /oauth/consent/ paths only.
// ---------------------------------------------------------------------------

export const RETURN_COOKIE = "j360_next";
export const RETURN_COOKIE_MAX_AGE = 1800;
const CONSENT_PREFIX = "/oauth/consent/";

/** True for a safe same-origin path under /oauth/consent/. */
export function isConsentPath(p: string | null | undefined): p is string {
  return isSafePath(p) && p.startsWith(CONSENT_PREFIX) && p.length > CONSENT_PREFIX.length;
}

/** Browser only: read the return cookie; returns a validated consent path or null. */
export function readReturnCookie(): string | null {
  if (typeof document === "undefined") return null;
  try {
    for (const part of document.cookie.split(";")) {
      const [k, ...rest] = part.trim().split("=");
      if (k === RETURN_COOKIE) {
        const v = decodeURIComponent(rest.join("="));
        return isConsentPath(v) ? v : null;
      }
    }
  } catch {
    /* malformed cookie � ignore */
  }
  return null;
}

/** Browser only: delete the return cookie. */
export function clearReturnCookie(): void {
  if (typeof document === "undefined") return;
  document.cookie = `${RETURN_COOKIE}=; Path=/; Max-Age=0; SameSite=Lax`;
}

/**
 * Post-sign-in destination. An explicit safe `next` always wins; otherwise a
 * valid consent return cookie; otherwise /applications. Clears the cookie.
 */
export function resolvePostLoginPath(next: string | null | undefined): string {
  const fromCookie = readReturnCookie();
  clearReturnCookie();
  return isSafePath(next) ? next : (fromCookie ?? "/applications");
}
