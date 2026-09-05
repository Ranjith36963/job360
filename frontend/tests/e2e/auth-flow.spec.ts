import { test, expect } from "@playwright/test";

/**
 * Auth flow E2E smoke — tests the middleware 307 redirect for protected routes.
 * Runs against the Next.js dev server at http://localhost:3000.
 */
test.describe("Auth middleware", () => {
  // Slice 5 (delete-sourcing-era) removed the dashboard route entirely —
  // there is no shared catalog left to browse (VISION rule 4). Next.js's own
  // router 404s here; no middleware branch is involved any more.
  test("anonymous visit to /dashboard 404s (route no longer exists)", async ({ page }) => {
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
