import { test, expect } from "@playwright/test";

/**
 * Pipeline page E2E smoke — checks that:
 * 1. Unauthenticated visit to /pipeline redirects to /login (middleware).
 * 2. Authenticated visit with mocked pipeline API shows the Kanban board.
 */

const MOCK_APPLICATIONS = {
  applications: [
    {
      id: 1,
      job_id: 42,
      user_id: "test-user-id",
      stage: "applied",
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      notes: null,
      reminder_at: null,
      title: "ML Engineer",
      company: "TechCo",
      apply_url: "https://example.com",
      salary: null,
    },
  ],
};

test.describe("Pipeline page", () => {
  test("anonymous visit redirects to /login", async ({ page }) => {
    await page.goto("/pipeline");
    await expect(page).toHaveURL(/\/login/);
  });

  test("authenticated visit shows pipeline heading", async ({
    page,
    context,
  }) => {
    await context.addCookies([
      {
        name: "job360_session",
        value: "smoke-test-token",
        domain: "localhost",
        path: "/",
      },
    ]);

    await page.route("**/api/pipeline**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_APPLICATIONS),
      })
    );

    await page.goto("/pipeline");
    await expect(page).not.toHaveURL(/\/login/);

    // Pipeline heading
    await expect(
      page.getByRole("heading", { name: /pipeline/i })
    ).toBeVisible({ timeout: 10_000 });
  });

  test("applied card appears in the pipeline board", async ({
    page,
    context,
  }) => {
    await context.addCookies([
      {
        name: "job360_session",
        value: "smoke-test-token",
        domain: "localhost",
        path: "/",
      },
    ]);

    // The page fetches THREE endpoints (applications + counts + reminders) via
    // Promise.all. Mock each with its correct shape — register the specific ones
    // AFTER the general one so they win (Playwright: most-recently-added wins).
    await page.route("**/api/pipeline**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_APPLICATIONS) })
    );
    await page.route("**/api/pipeline/counts**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ applied: 1, outreach: 0, interview: 0, offer: 0, rejected: 0 }) })
    );
    await page.route("**/api/pipeline/reminders**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ reminders: [] }) })
    );

    await page.goto("/pipeline");

    // The mocked job title appears in the board. KanbanBoard renders BOTH a
    // desktop and a mobile layout (one hidden via CSS), so the text resolves to
    // 2 nodes — use .first() to avoid a strict-mode "2 elements" failure.
    await expect(page.getByText("ML Engineer").first()).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("TechCo").first()).toBeVisible();
  });
});
