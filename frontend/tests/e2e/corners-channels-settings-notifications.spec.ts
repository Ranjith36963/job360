import { test, expect } from "@playwright/test";

/**
 * UI coverage for the three pages the existing e2e suite missed:
 * channels, settings, notifications-ledger. Each checks the middleware gate
 * (anon → /login) and an authenticated render (fake session cookie + mocked
 * API), matching the self-contained pattern of the other specs.
 */

const COOKIE = {
  name: "job360_session",
  value: "smoke-test-token",
  domain: "localhost",
  path: "/",
};

test.describe("Channels page", () => {
  test("anonymous visit redirects to /login", async ({ page }) => {
    await page.goto("/channels");
    await expect(page).toHaveURL(/\/login/);
  });

  test("authenticated visit shows the channels heading", async ({ page, context }) => {
    await context.addCookies([COOKIE]);
    await page.route("**/api/settings/channels**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: "[]" })
    );
    await page.goto("/channels");
    await expect(page).not.toHaveURL(/\/login/);
    // h1 is "Notification channels" (there's also an h2 "Configured channels").
    await expect(page.getByRole("heading", { name: /notification channels/i })).toBeVisible({ timeout: 10_000 });
  });
});

test.describe("Notifications ledger page", () => {
  test("anonymous visit redirects to /login", async ({ page }) => {
    await page.goto("/notifications");
    await expect(page).toHaveURL(/\/login/);
  });

  test("authenticated visit shows the notification history heading", async ({ page, context }) => {
    await context.addCookies([COOKIE]);
    await page.route("**/api/notifications/stats**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ sent: 0, failed: 0, queued: 0 }) })
    );
    await page.route("**/api/notifications**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ notifications: [] }) })
    );
    await page.goto("/notifications");
    await expect(page).not.toHaveURL(/\/login/);
    await expect(page.getByRole("heading", { name: /notification history/i })).toBeVisible({ timeout: 10_000 });
  });
});

test.describe("Settings page", () => {
  test("anonymous visit redirects to /login", async ({ page }) => {
    await page.goto("/settings");
    await expect(page).toHaveURL(/\/login/);
  });

  test("authenticated visit is not bounced to /login", async ({ page, context }) => {
    await context.addCookies([COOKIE]);
    await page.route("**/api/**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: "{}" })
    );
    await page.goto("/settings");
    await expect(page).not.toHaveURL(/\/login/);
  });
});
