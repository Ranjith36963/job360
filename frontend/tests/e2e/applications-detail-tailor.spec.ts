import { test, expect, type Page } from "@playwright/test";

/**
 * The web tailor fallback lives on the application page now — the sourcing
 * era's job pages are gone (docs/plans/2026-09-05-delete-sourcing-era,
 * spec.md R9). Pinned here:
 *
 *   1. `/applications/{id}` renders the tailor section (TailorSection),
 *      reachable without ever visiting a `/jobs/{id}` page.
 *   2. `/jobs/1` and `/dashboard` — the deleted sourcing routes — 404.
 *
 * Hermetic (frontend-only), same pattern as applications-home.spec.ts: fake
 * the session cookie (middleware only checks presence) and mock every API
 * call with `page.route`.
 */

const SESSION_COOKIE = {
  name: "job360_session",
  value: "e2e-token",
  domain: "localhost",
  path: "/",
};

const APPLICATION_ID = 8181;
const JOB_ID = 555;

function applicationDetail() {
  return {
    id: APPLICATION_ID,
    job_id: JOB_ID,
    status: "considering",
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    last_event_at: "2026-09-01T00:00:00Z",
    job: {
      job_title: "Platform Engineer",
      job_company: "Northwind",
      job_location: "Remote",
      job_url: "https://northwind.example/careers/9",
      job_source: "user_brought",
      job_description_snapshot: "Build the platform. Kubernetes, Go, Postgres.",
      snapshot_at: "2026-09-01T00:00:00Z",
      catalog_present: true,
    },
    fit: null,
    artifacts: [],
    events: [
      {
        id: 1,
        event_type: "brought",
        detail: "",
        payload: {},
        occurred_at: "2026-09-01T00:00:00Z",
        recorded_at: "2026-09-01T00:00:00Z",
        recorded_by: "web",
        corrects_event_id: null,
        superseded: false,
      },
    ],
    receipts: [],
  };
}

async function mockBackend(page: Page) {
  await page.route("**/api/auth/me**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ id: "e2e-user", email: "e2e@example.com" }),
    })
  );

  await page.route(`**/api/applications/${APPLICATION_ID}`, (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(applicationDetail()),
    });
  });
}

test.describe("Application detail — the tailor fallback moved here (R9)", () => {
  test("renders the tailor section without ever visiting a job page", async ({
    page,
    context,
  }) => {
    await context.addCookies([SESSION_COOKIE]);
    await mockBackend(page);

    await page.goto(`/applications/${APPLICATION_ID}`);

    await expect(page.getByText("Platform Engineer")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText("Northwind")).toBeVisible();

    // The tailor fallback (TailorSection) — two entry points, CV and cover letter.
    await expect(
      page.getByRole("heading", { name: /tailor my ats-friendly cv/i })
    ).toBeVisible();
    await expect(page.getByRole("button", { name: /tailor my cv/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /create cover letter/i })).toBeVisible();

    // "View ad" reads the job's own URL — no dependency on a /jobs/{id} page.
    await expect(page.getByRole("link", { name: /view ad/i })).toHaveAttribute(
      "href",
      "https://northwind.example/careers/9"
    );
  });

  test("/jobs/1 no longer exists", async ({ page, context }) => {
    await context.addCookies([SESSION_COOKIE]);
    const response = await page.goto("/jobs/1");
    expect(response?.status()).toBe(404);
  });

  test("/dashboard no longer exists", async ({ page, context }) => {
    await context.addCookies([SESSION_COOKIE]);
    const response = await page.goto("/dashboard");
    expect(response?.status()).toBe(404);
  });
});
