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
function isSafePath(p: string | null | undefined): p is string {
  return !!p && p.startsWith("/") && !p.startsWith("//") && !/[\\\t\n\r]/.test(p);
}

/**
 * Validates the ?next param to prevent open-redirect attacks. Only allows
 * paths that start with "/" but not "//" (protocol-relative); anything else
 * (external URL, missing, malformed) falls back to /dashboard.
 */
export function safeNext(p: string | null | undefined): string {
  return isSafePath(p) ? p : "/dashboard";
}

/**
 * Same safety check as `safeNext`, but returns `undefined` instead of a
 * fallback — for callers that want to OMIT an unsafe/missing `next` rather
 * than substitute a default (e.g. the magic-link request body: no `next`
 * means "no preference", not "go to /dashboard").
 */
export function safeNextOrUndefined(p: string | null | undefined): string | undefined {
  return isSafePath(p) ? p : undefined;
}
