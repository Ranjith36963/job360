import { test, expect } from "@playwright/test";

/**
 * Auth flow E2E smoke — tests the middleware 307 redirect for protected routes.
 * Runs against the Next.js dev server at http://localhost:3000.
 */
test.describe("Auth middleware", () => {
  // R12 (docs/plans/2026-09-04-application-spine) — the legacy search UI
  // (Dashboard) is behind NEXT_PUBLIC_SEARCH_UI_ENABLED, default OFF, and
  // this project does not set it — so /dashboard 404s regardless of auth
  // state. Unlike the backend flag (settings.py, monkeypatchable per test),
  // NEXT_PUBLIC_* is inlined at BUILD TIME (spec C2), so this project cannot
  // exercise both flag positions in one dev-server run — the OFF position
  // (this test) is what a default production build actually serves;
  // backend/tests/test_search_flag.py pins BOTH positions where it can.
  // KNOWN CONSEQUENCE, not a bug introduced here: dashboard-sort.spec.ts,
  // feed-visibility.spec.ts, job-render.spec.ts and
  // two-account-isolation.spec.ts all navigate to /dashboard and will now
  // fail against a default build — the dashboard they exercise is the exact
  // feature R12 hides (and slice 5 deletes). Flagged in the build report,
  // not silently fixed by flipping the default back on.
  test("anonymous visit to /dashboard 404s (search UI flag is off by default)", async ({ page }) => {
    const response = await page.goto("/dashboard");
    expect(response?.status()).toBe(404);
  });

  test("anonymous visit to /applications redirects to /login", async ({ page }) => {
    await page.goto("/applications");
    await expect(page).toHaveURL(/\/login/);
  });

  test("anonymous visit to /profile redirects to /login", async ({ page }) => {
    await page.goto("/profile");
    await expect(page).toHaveURL(/\/login/);
  });

  test("anonymous visit to /pipeline redirects to /login", async ({ page }) => {
    await page.goto("/pipeline");
    await expect(page).toHaveURL(/\/login/);
  });

  test("home page loads without redirect", async ({ page }) => {
    await page.goto("/");
    // Should NOT be redirected to /login
    await expect(page).not.toHaveURL(/\/login/);
  });
});
