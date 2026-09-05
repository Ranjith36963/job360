// The route map the design pass walks.
//
// Kept in its own file so it is the ONE place a new page gets registered — the
// runner, the contact sheet and any future guard all read this list. Derived
// from `frontend/src/app/**/page.tsx` (14 routes, 2026-09-05).
//
// `auth: true`  -> the middleware redirects an anonymous visitor to /login.
//                  Needs DESIGN_SESSION (a real job360_session cookie) or the
//                  local E2E_TEST_MODE bypass, else the shot is the login page.
// `skip: true`  -> deliberately not part of a design review (dev-only probes).
// `dynamic`     -> the path has a [param]; `resolve` fills it at run time.

export const ROUTES = [
  // ── public ────────────────────────────────────────────────────────────────
  { path: "/", name: "landing" },
  { path: "/login", name: "login" },
  { path: "/register", name: "register" },
  { path: "/forgot-password", name: "forgot-password" },
  { path: "/reset-password", name: "reset-password" },
  { path: "/verify-email", name: "verify-email" },
  { path: "/privacy", name: "privacy" },
  { path: "/terms", name: "terms" },
  { path: "/contact", name: "contact" },

  // ── authed ────────────────────────────────────────────────────────────────
  // /dashboard, /jobs, /jobs/:id and /admin/sources were deleted in slice 5
  // (delete-sourcing-era) — Job360 never sources or ranks jobs (VISION rule 4),
  // so there is no catalog left to review. /pipeline, /channels, /notifications
  // and /settings/notifications were deleted in the mission sweep — notifications
  // are pull-not-push (VISION:133) and the Kanban /pipeline folded into
  // /applications.
  { path: "/profile", name: "profile", auth: true },
  { path: "/applications", name: "applications", auth: true },
  { path: "/settings", name: "settings", auth: true },
  { path: "/settings/account", name: "settings-account", auth: true },

  // ── not reviewed ──────────────────────────────────────────────────────────
  // /auth/magic consumes a one-time token and redirects; screenshotting it
  // without a token only ever captures the error branch, which the login and
  // verify-email shots already cover.
  { path: "/auth/magic", name: "auth-magic", skip: "consumes a one-time token" },
];

export const VIEWPORTS = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "mobile", width: 390, height: 844 },
];

// Dark only, on purpose. `src/app/globals.css:96` says "Always dark — the neon
// lime theme IS dark", and its `:root` and `.dark` blocks define byte-identical
// tokens. Measured against live prod: seeding job360-theme=light does flip
// <html class> from `dark` to `light`, and body's computed background is
// unchanged (`lab(0.317284 -0.156149 0.11797)` either way). So a light-mode pass
// is 50% of the run producing pixel-identical images. Add "light" back here the
// day globals.css grows a real light palette.
export const THEMES = ["dark"];
