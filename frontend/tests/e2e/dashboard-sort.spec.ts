import { test, expect, type Page } from "@playwright/test";

/**
 * Dashboard sort order.
 *
 * Requirement: the job list is always shown highest-score → lowest, in EVERY
 * time-bucket tab (All / 24h / 48h / 3d / 5d / 7d). To prove the UI guarantees
 * this regardless of server order, we mock /api/jobs with deliberately
 * UNSORTED scores and assert the rendered badges come out descending — then
 * re-assert after clicking each tab.
 *
 * Hermetic (frontend-only, like the other e2e specs): fake the session cookie
 * (middleware only checks presence) and mock the dashboard's API calls.
 */

const SESSION_COOKIE = {
  name: "job360_session",
  value: "e2e-token",
  domain: "localhost",
  path: "/",
};

// Deliberately jumbled scores. Sorted-desc expectation: 78,66,54,39,35,31.
const SCORES_UNSORTED = [39, 78, 31, 54, 35, 66];

function makeJob(id: number, score: number) {
  return {
    id,
    title: `Test Job ${id}`,
    company: `Company ${id}`,
    location: "London, UK",
    salary: null,
    match_score: score,
    source: "reed",
    date_found: new Date().toISOString(),
    apply_url: `https://example.test/${id}`,
    visa_flag: false,
    experience_level: "mid",
    role: 0,
    skill: 0,
    seniority_score: 0,
    experience: 0,
    credentials: 0,
    location_score: 0,
    recency: 0,
    semantic: 0,
    penalty: 0,
    action: null,
    bucket: "24h",
    posted_at: null,
    first_seen_at: null,
    last_seen_at: null,
    date_confidence: "high",
    staleness_state: "active",
  };
}

async function mockDashboard(page: Page) {
  const jobs = SCORES_UNSORTED.map((s, i) => makeJob(i + 1, s));
  await page.route("**/api/auth/me**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ id: "e2e-user", email: "e2e@example.com" }),
    })
  );
  await page.route("**/api/status**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ jobs_total: jobs.length, sources_total: 50 }),
    })
  );
  // Every /api/jobs request (filtered list + the unfiltered count query, and
  // every bucket-tab refetch) returns the SAME jumbled set.
  await page.route("**/api/jobs**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        jobs,
        total: jobs.length,
        filters_applied: {},
      }),
    })
  );
}

async function renderedScores(page: Page): Promise<number[]> {
  // JobCard's score badge is labelled "AI fit score: N" (judged) or "Keyword
  // match score: N" (keyword) — match both via the shared " score: " infix.
  return page.$$eval('[aria-label*=" score: "]', (els) =>
    els.map((el) => Number((el.getAttribute("aria-label")!.match(/\d+/) || [])[0]))
  );
}

function isDescending(arr: number[]): boolean {
  return arr.every((v, i) => i === 0 || arr[i - 1] >= v);
}

test.describe("Dashboard job sort order", () => {
  const TABS = ["All", "24h", "48h", "3d", "5d", "7d"];

  test("jobs render highest-score-first regardless of server order", async ({
    page,
    context,
  }) => {
    await context.addCookies([SESSION_COOKIE]);
    await mockDashboard(page);

    await page.goto("/dashboard");

    // Wait for the list to populate (cold-compile tolerant).
    await expect
      .poll(async () => (await renderedScores(page)).length, { timeout: 40_000 })
      .toBe(SCORES_UNSORTED.length);

    const scores = await renderedScores(page);
    expect(scores, `rendered order: ${scores}`).toEqual([78, 66, 54, 39, 35, 31]);
    expect(isDescending(scores)).toBe(true);
  });

  test("sort holds in every time-bucket tab", async ({ page, context }) => {
    await context.addCookies([SESSION_COOKIE]);
    await mockDashboard(page);

    await page.goto("/dashboard");
    await expect
      .poll(async () => (await renderedScores(page)).length, { timeout: 40_000 })
      .toBe(SCORES_UNSORTED.length);

    for (const tab of TABS) {
      await page.getByRole("button", { name: new RegExp(`^${tab}\\b`, "i") }).click();
      // Clicking a tab triggers a refetch that briefly EMPTIES the list while
      // the new bucket loads. Poll on the full expected array — NOT on
      // isDescending(), which is vacuously true for the transient [] and would
      // let the wait exit mid-refetch (the flake this spec was quarantined for).
      await expect
        .poll(async () => await renderedScores(page), {
          timeout: 30_000,
          message: `tab ${tab} never settled to sorted order`,
        })
        .toEqual([78, 66, 54, 39, 35, 31]);
    }
  });
});
