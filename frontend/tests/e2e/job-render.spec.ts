import { test, expect } from "@playwright/test";

/**
 * Job render E2E smoke — value-presence: navigates to /dashboard with a mocked
 * session cookie and mocked jobs API, then asserts the UI shows job data.
 * Does not require the real backend; uses Playwright route mocking for browser
 * fetch calls. The Next.js server-side /api/status call may 503 (gracefully ignored).
 */

const MOCK_JOB = {
  id: 1,
  title: "Senior ML Engineer",
  company: "DeepTech Ltd",
  location: "London, UK",
  match_score: 87,
  date_found: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
  source: "adzuna",
  apply_url: "https://example.com/apply",
  action: null,
  salary: "£70,000 – £90,000",
  salary_min: 70000,
  salary_max: 90000,
  salary_currency: "GBP",
  employment_type: "FULL_TIME",
  workplace_type: "hybrid",
  seniority: "senior",
  seniority_score: 8,
  title_score: 35,
  skill_score: 30,
  location_score: 8,
  recency_score: 9,
  salary_score: 7,
  visa_score: 5,
  workplace_score: 6,
  visa_sponsorship: false,
  is_stale: false,
  staleness_state: "fresh",
  industry: "AI/ML",
  matched_skills: ["Python", "PyTorch", "LLMs"],
  required_skills: ["Python", "MLOps"],
  title_canonical: "Machine Learning Engineer",
  description: "An exciting ML role.",
  posted_at: null,
  first_seen: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
  last_seen: new Date().toISOString(),
  dedup_group_ids: null,
  years_experience_min: 3,
};

test.describe("Dashboard job render", () => {
  test.skip(
    process.env.NEXT_PUBLIC_SEARCH_UI_ENABLED !== "true",
    "legacy search UI is off by default (slice 2, R12); dies with slice 5"
  );

  test.beforeEach(async ({ context }) => {
    // Bypass middleware by providing a session cookie
    await context.addCookies([
      {
        name: "job360_session",
        value: "smoke-test-token",
        domain: "localhost",
        path: "/",
      },
    ]);
  });

  test("dashboard loads and shows job count", async ({ page }) => {
    // Mock the jobs API (called client-side by TanStack Query)
    await page.route("**/api/jobs**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ jobs: [MOCK_JOB], total: 1 }),
      })
    );

    // Mock status API
    await page.route("**/api/status**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          jobs_total: 1,
          last_run: null,
          sources_active: 50,
          sources_total: 50,
          profile_exists: false,
        }),
      })
    );

    await page.goto("/dashboard");

    // Should NOT be redirected to /login (cookie works)
    await expect(page).not.toHaveURL(/\/login/);

    // Dashboard heading is present
    await expect(page.getByRole("heading", { name: /dashboard/i })).toBeVisible();

    // Job card for the mocked job appears
    await expect(page.getByText("Senior ML Engineer")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("DeepTech Ltd")).toBeVisible();
  });

  test("job card shows match score badge", async ({ page }) => {
    await page.route("**/api/jobs**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ jobs: [MOCK_JOB], total: 1 }),
      })
    );
    await page.route("**/api/status**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ jobs_total: 1, last_run: null, sources_active: 50, sources_total: 50, profile_exists: false }) })
    );

    await page.goto("/dashboard");
    // Match score 87 should be visible somewhere (badge or stat)
    await expect(page.getByText(/87/)).toBeVisible({ timeout: 10_000 });
  });
});
