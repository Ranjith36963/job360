import { test, expect, type Page } from "@playwright/test";

/**
 * Feed visibility — the filter must never hide the product silently.
 *
 * THE BUG (measured in prod 2026-07-28, not hypothesised):
 *   user 88e7d907 had 3,236 feed rows and saw 406 of them — 87.5% hidden —
 *   because DEFAULT_MIN_SCORE was 20 while the score distribution was
 *   p50=10, p90=26, max=58. The floor sat above the 90th percentile.
 *   Nothing on screen said anything was hidden, so a heavily-filtered feed
 *   looked exactly like a product that had found nothing.
 *
 * Two guarantees pinned here:
 *   1. Out of the box, NOTHING is filtered out. Rank, never hide.
 *   2. If the user does set a floor, the UI says how many that floor removed
 *      and offers a way back. A hidden job with nothing announcing it is
 *      indistinguishable from a job that does not exist.
 *
 * Hermetic (frontend-only), same pattern as dashboard-sort.spec.ts: fake the
 * session cookie (middleware only checks presence) and mock the API.
 */

const SESSION_COOKIE = {
  name: "job360_session",
  value: "e2e-token",
  domain: "localhost",
  path: "/",
};

// Mirrors the real prod histogram shape: a big mass at 10-20, a thin tail, and
// a maximum well under any legacy threshold.
const PROD_LIKE_SCORES = [12, 14, 16, 18, 22, 26, 34, 41, 55];
const LOW_SCORE_COUNT = PROD_LIKE_SCORES.filter((s) => s < 20).length; // 4

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

/**
 * Mock /api/jobs so it HONOURS min_score the way the real backend now does:
 * filter the rows, but always report `total_unfiltered` so the UI can say what
 * it hid. Reading min_score off the query string is the point — it lets the
 * test drive the real filter path rather than asserting on a fixed payload.
 */
async function mockDashboard(page: Page) {
  const all = PROD_LIKE_SCORES.map((s, i) => makeJob(i + 1, s));

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
      body: JSON.stringify({ jobs_total: all.length, sources_total: 50 }),
    })
  );
  await page.route("**/api/jobs**", (route) => {
    const min = Number(new URL(route.request().url()).searchParams.get("min_score") ?? 0);
    const shown = all.filter((j) => j.match_score >= min);
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        jobs: shown,
        total: shown.length,
        total_unfiltered: all.length,
        filters_applied: min ? { min_score: min } : {},
      }),
    });
  });
}

function renderedCount(page: Page): Promise<number> {
  return page.$$eval('[aria-label*="score: "]', (els) => els.length);
}

test.describe("Feed visibility", () => {
  test.skip(
    process.env.NEXT_PUBLIC_SEARCH_UI_ENABLED !== "true",
    "legacy search UI is off by default (slice 2, R12); dies with slice 5"
  );

  test("shows EVERY job by default — the floor no longer hides the product", async ({
    page,
    context,
  }) => {
    await context.addCookies([SESSION_COOKIE]);
    await mockDashboard(page);

    const requested: string[] = [];
    page.on("request", (r) => {
      if (r.url().includes("/api/jobs")) requested.push(r.url());
    });

    await page.goto("/dashboard");
    await expect(page.locator('[aria-label*="score: "]').first()).toBeVisible({
      timeout: 20_000,
    });

    // 1. Every job rendered, including the ones under the old floor of 20.
    expect(await renderedCount(page)).toBe(PROD_LIKE_SCORES.length);

    // 2. And the request itself asked for no floor. This is the real assertion:
    //    a UI that rendered everything but still SENT min_score=20 would pass a
    //    naive count check while remaining broken against a bigger feed.
    const withFloor = requested.filter((u) => {
      const v = new URL(u).searchParams.get("min_score");
      return v !== null && Number(v) > 0;
    });
    expect(withFloor, `no /api/jobs call may impose a floor: ${withFloor[0]}`).toHaveLength(0);

    // 3. Nothing hidden means no "hidden by filters" notice.
    await expect(page.getByText(/hidden by filters/i)).toHaveCount(0);
  });

  test("announces hidden rows and offers a way back when the API reports them", async ({
    page,
    context,
  }) => {
    await context.addCookies([SESSION_COOKIE]);

    // Mock a server that HAS filtered rows out: it returns fewer jobs than it
    // says exist. This is the exact contract the backend now honours via
    // `total_unfiltered`, and the only thing the UI can act on. Driving it this
    // way (rather than through the slider) keeps the test about the guarantee —
    // "never hide silently" — instead of about widget mechanics.
    const all = PROD_LIKE_SCORES.map((s, i) => makeJob(i + 1, s));
    const shown = all.filter((j) => j.match_score >= 20);
    const hidden = all.length - shown.length;

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
        body: JSON.stringify({ jobs_total: all.length, sources_total: 50 }),
      })
    );
    await page.route("**/api/jobs**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          jobs: shown,
          total: shown.length,
          total_unfiltered: all.length,
          filters_applied: { min_score: 20 },
        }),
      })
    );

    await page.goto("/dashboard");
    // Wait for the list to actually render before counting anything — counting
    // an un-rendered page is how the first version of this test fooled itself.
    await expect(page.locator('[aria-label*="score: "]').first()).toBeVisible({
      timeout: 30_000,
    });
    expect(await renderedCount(page)).toBe(shown.length);

    // THE GUARANTEE: the missing rows are announced, by number.
    expect(hidden).toBeGreaterThan(0);
    await expect(page.getByText(/hidden by filters/i)).toBeVisible();
    await expect(page.getByText(String(hidden), { exact: false }).first()).toBeVisible();

    // And there is a visible way back.
    await expect(page.getByRole("button", { name: /show all/i })).toBeVisible();
  });
});
