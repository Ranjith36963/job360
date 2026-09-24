import { test, expect, type Page } from "@playwright/test";

/**
 * The fit picture (alignment view) on the application page: the stored fit
 * verdict plus which of the user's own profile skills occur in the ad text.
 * `GET /api/applications/{id}/alignment` — built in parallel, hand-typed on
 * the frontend (`src/lib/api.ts`'s `Alignment`), not yet in the generated
 * `api-types.ts`.
 *
 * Also covers the removal of the "What's new" strip (every event already
 * lives in its application's timeline; the list is the "what's new").
 *
 * Hermetic (frontend-only): fake the session cookie and mock every API call
 * with `page.route`, same pattern as applications-detail-diff.spec.ts.
 */

const SESSION_COOKIE = {
  name: "job360_session",
  value: "e2e-token",
  domain: "localhost",
  path: "/",
};

const APPLICATION_ID = 8383;
const JOB_ID = 707;

function applicationDetail() {
  return {
    id: APPLICATION_ID,
    job_id: JOB_ID,
    status: "considering",
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-20T09:04:11Z",
    last_event_at: "2026-09-20T09:04:11Z",
    job: {
      job_title: "NLP Engineer",
      job_company: "Northwind",
      job_location: "Remote",
      job_url: "https://northwind.example/careers/12",
      job_source: "user_brought",
      job_description_snapshot: "Build the retrieval pipeline. Python, RAG, LangGraph.",
      snapshot_at: "2026-09-01T00:00:00Z",
      catalog_present: true,
    },
    fit: {
      score: 68,
      verdict: "Strong match on NLP",
      gaps: ["leading university"],
      reasoning: null,
      recorded_by: "agent:Claude",
      recorded_at: "2026-09-20T09:04:11Z",
    },
    next_step: { code: "apply", label: "CV ready — apply, then mark it applied" },
    artifacts: [],
    contacts: [],
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
        source: null,
        scheduled_at: null,
      },
    ],
    receipts: [],
  };
}

function alignmentPayload() {
  return {
    fit: {
      score: 68,
      verdict: "Strong match on NLP",
      gaps: ["leading university"],
      reasoning: null,
      axes: [
        { name: "LLM apps & frontier APIs", role: 90, you: 80 },
        { name: "RAG & retrieval design", role: 85, you: 80 },
        { name: "NLP / CV modelling depth", role: 70, you: 65 },
        { name: "Production Python & deployment", role: 85, you: 65 },
        { name: "CS fundamentals (distributed, HPC)", role: 75, you: 40 },
        { name: "Client-facing delivery", role: 55, you: 35 },
      ],
      recorded_by: "agent:Claude",
      recorded_at: "2026-09-20T09:04:11Z",
    },
    skills_in_ad: ["Python", "RAG"],
    skills_not_in_ad: ["LangGraph"],
    skills_total: 3,
    ad_chars: 900,
  };
}

async function mockAuth(page: Page) {
  await page.route("**/api/auth/me**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ id: "e2e-user", email: "e2e@example.com" }),
    })
  );
}

test.describe("Alignment — the fit picture on the application page", () => {
  test("shows the score bar, verdict, gaps, and the skill columns", async ({ page, context }) => {
    await context.addCookies([SESSION_COOKIE]);
    await mockAuth(page);

    await page.route(`**/api/applications/${APPLICATION_ID}`, (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(applicationDetail()),
      });
    });

    await page.route(`**/api/applications/${APPLICATION_ID}/alignment`, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(alignmentPayload()),
      })
    );

    await page.goto(`/applications/${APPLICATION_ID}`);
    await expect(page.getByText("NLP Engineer")).toBeVisible({ timeout: 20_000 });

    await expect(page.getByTestId("next-step")).toHaveText(
      "Next: CV ready — apply, then mark it applied"
    );

    const bar = page.getByTestId("fit-score-bar").locator("> div");
    await expect(bar).toHaveCSS("width", /.+/);
    const width = await bar.evaluate((el) => (el as HTMLElement).style.width);
    expect(width).toBe("68%");

    await expect(page.getByText("Strong match on NLP")).toBeVisible();
    await expect(page.getByTestId("fit-gap")).toHaveCount(1);

    // The radar: one shape per side, one label per axis the agent named.
    const radar = page.getByTestId("fit-radar");
    await expect(radar).toBeVisible();
    await expect(radar.getByTestId("fit-radar-axis")).toHaveCount(6);
    await expect(radar.getByTestId("fit-radar-role")).toHaveAttribute("points", /.+/);
    await expect(radar.getByTestId("fit-radar-you")).toHaveAttribute("points", /.+/);
    await expect(radar.locator("figcaption").getByText("The role asks", { exact: true })).toBeVisible();

    await expect(
      page.getByText("2 of 3 of your skills appear in this ad")
    ).toBeVisible();

    const inAd = page.getByTestId("skills-in-ad");
    await expect(inAd.locator("li")).toHaveCount(2);
    const notInAd = page.getByTestId("skills-not-in-ad");
    await expect(notInAd.locator("li")).toHaveCount(1);
  });
});

test.describe("Applications list — no What's-new strip", () => {
  test("the list page shows no 'What's new' text and no whats-new element", async ({
    page,
    context,
  }) => {
    await context.addCookies([SESSION_COOKIE]);
    await mockAuth(page);

    await page.route("**/api/applications?**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          applications: [
            {
              id: APPLICATION_ID,
              job_id: JOB_ID,
              job_title: "NLP Engineer",
              job_company: "Northwind",
              job_url: "https://northwind.example/careers/12",
              status: "considering",
              last_event_at: "2026-09-20T09:04:11Z",
              events: 1,
              artifacts: {},
              receipts: 0,
              next_step: { code: "judge_fit", label: "No fit judged yet — ask your agent to judge it" },
            },
          ],
          total: 1,
        }),
      })
    );

    await page.goto("/applications");
    await expect(page.getByText(/nlp engineer/i).first()).toBeVisible({ timeout: 20_000 });

    await expect(page.getByText("What's new")).toHaveCount(0);
    await expect(page.locator('[data-testid="whats-new"]')).toHaveCount(0);
  });
});
