// A FULL page load, not a client navigation: it drops the Next router cache,
// component state and module-level state, so nothing private stays on screen
// after a session ends. Its own module so tests can mock it (jsdom cannot navigate).
export function hardNavigate(url: string): void {
  window.location.assign(url);
}

/** Reload the current page from the server (same reason: mockable in tests). */
export function hardReload(): void {
  window.location.reload();
}

/** Where a session that ended mid-visit sends the user (point 3 contract). */
export function expiredLoginUrl(pathAndSearch: string): string {
  if (pathAndSearch === "/" || pathAndSearch === "") return "/login?reason=expired";
  return `/login?reason=expired&next=${encodeURIComponent(pathAndSearch)}`;
}

const AUTH_PAGES = [
  "/login",
  "/register",
  "/forgot-password",
  "/reset-password",
  "/verify-email",
  "/undo-email-change",
  "/auth/magic",
];
const PUBLIC_PAGES = ["/privacy", "/terms", "/trust", "/contact"];

/**
 * Auth and public pages: a 401 there is not "your session ended".
 *
 * "/" is NOT in this list on purpose. The signed-out landing page makes no
 * private API call, so a 401 on "/" can only come from the signed-in home
 * page, and that one must be reloaded like any other private page.
 */
export function isAuthOrPublicPath(pathname: string): boolean {
  return [...AUTH_PAGES, ...PUBLIC_PAGES].some(
    (p) => pathname === p || pathname.startsWith(`${p}/`)
  );
}

/** Pages a signed-in user is sent away from (to `safeNext(next)`). */
export const SIGNED_OUT_ONLY_PAGES = ["/login", "/register", "/forgot-password"];
