import { test, expect } from "@playwright/test";

/**
 * Bring a job — the front door onto the application spine
 * (docs/plans/2026-09-05-delete-sourcing-era, intent.md/spec.md T10).
 *
 * 1. Anonymous /bring and /receipts redirect to /login (middleware).
 * 2. Paste an ad on /bring → the backend stores it and births the
 *    Application (no score, no feed row) → land on /applications/{id} with
 *    the brought job showing and the tailor fallback reachable.
 * 3. /receipts still works read-only for the (separate) job-scoped receipt
 *    history — unaffected by this slice.
 *
 * The full brought -> applied -> two-CV-versions journey through the
 * application spine is covered end to end by applications-home.spec.ts; this
 * spec only proves the /bring form + redirect wiring, so it does not repeat
 * that flow. The backend is mocked with page.route — this proves the UI
 * wiring, not the database. The Postgres truth is
 * backend/tests/test_bring_a_job.py.
 */

const AD_TEXT =
  "We are hiring a Senior Python Engineer to build our matching engine.\n\nYou will own FastAPI services and Postgres.";

const APPLICATION_ID = 5501;

// Mirrors backend/src/api/models.py JobResponse post-slice-5: no score, no
// dims, no enrichment_applied (spec R3).
const BROUGHT_JOB = {
  id: 9001,
  title: "Senior Python Engineer",
  company: "Acme Ltd",
  location: "London, UK",
  salary: null,
  source: "user_brought",
  date_found: new Date().toISOString(),
  apply_url: "https://careers.example.com/jobs/123",
  visa_flag: false,
  visa_status: "unknown",
  job_type: "",
  experience_level: "",
  description: AD_TEXT,
  posted_at: null,
  date_confidence: null,
};

// Mirrors ApplicationDetailOut for a freshly-brought application: one
// "brought" event, no artifacts yet, no receipts yet.
const APPLICATION_DETAIL = {
  id: APPLICATION_ID,
  job_id: BROUGHT_JOB.id,
  status: "considering",
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  last_event_at: new Date().toISOString(),
  job: {
    job_title: BROUGHT_JOB.title,
    job_company: BROUGHT_JOB.company,
    job_location: BROUGHT_JOB.location,
    job_url: BROUGHT_JOB.apply_url,
    job_source: "user_brought",
    job_description_snapshot: AD_TEXT,
    snapshot_at: new Date().toISOString(),
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
      occurred_at: new Date().toISOString(),
      recorded_at: new Date().toISOString(),
      recorded_by: "web",
      corrects_event_id: null,
      superseded: false,
    },
  ],
  receipts: [],
};

const SESSION_COOKIE = {
  name: "job360_session",
  value: "smoke-test-token",
  domain: "localhost",
  path: "/",
};

const json = (body: unknown, status = 200) => ({
  status,
  contentType: "application/json",
  body: JSON.stringify(body),
});

test.describe("Bring a job", () => {
  test("anonymous /bring and /receipts redirect to /login", async ({ page }) => {
    await page.goto("/bring");
    await expect(page).toHaveURL(/\/login/);
    await page.goto("/receipts");
    await expect(page).toHaveURL(/\/login/);
  });

  test("paste an ad → application is born → lands on its application page", async ({
    page,
    context,
  }) => {
    await context.addCookies([SESSION_COOKIE]);

    await page.route("**/api/auth/me**", (route) =>
      route.fulfill(json({ id: "e2e-user", email: "e2e@example.com" }))
    );

    let bringBody: Record<string, unknown> | null = null;
    await page.route("**/api/jobs/bring", (route) => {
      bringBody = route.request().postDataJSON() as Record<string, unknown>;
      return route.fulfill(
        json({
          job: BROUGHT_JOB,
          existing: false,
          application_id: APPLICATION_ID,
          status: "considering",
        })
      );
    });
    await page.route(`**/api/applications/${APPLICATION_ID}`, (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      return route.fulfill(json(APPLICATION_DETAIL));
    });

    // --- 1. Paste the ad -------------------------------------------------
    await page.goto("/bring");
    await expect(page).not.toHaveURL(/\/login/);
    await expect(page.getByRole("heading", { name: "Bring a job" })).toBeVisible();

    const bringBtn = page.getByRole("button", { name: /bring this job/i });
    await expect(bringBtn).toBeDisabled(); // title, company, ad are required

    await page.getByLabel("Job title").fill(BROUGHT_JOB.title);
    await page.getByLabel("Company").fill(BROUGHT_JOB.company);
    await page.getByLabel(/^Location/).fill(BROUGHT_JOB.location);
    await page.getByLabel(/Link to the ad/).fill(BROUGHT_JOB.apply_url);
    await page.getByLabel("The ad, as written").fill(AD_TEXT);
    await expect(bringBtn).toBeEnabled();

    // No scoring copy survives on this page (VISION rule 4).
    await expect(page.getByText(/score.{0,20}profile/i)).toHaveCount(0);

    await bringBtn.click();

    // --- 2. Land on the application page, no /jobs/{id} involved --------
    await expect(page).toHaveURL(new RegExp(`/applications/${APPLICATION_ID}$`), {
      timeout: 10_000,
    });
    expect(bringBody).not.toBeNull();
    expect(bringBody!.title).toBe(BROUGHT_JOB.title);
    expect(bringBody!.description).toBe(AD_TEXT);
    // The bring response never carried a score field the page could echo.
    expect(bringBody).not.toHaveProperty("match_score");

    await expect(page.getByText(BROUGHT_JOB.title)).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(BROUGHT_JOB.company)).toBeVisible();

    // The tailor fallback lives on the application page now.
    await expect(
      page.getByRole("heading", { name: /tailor my ats-friendly cv/i })
    ).toBeVisible();
  });

  test("empty receipts page points to Bring a job", async ({ page, context }) => {
    await context.addCookies([SESSION_COOKIE]);
    await page.route("**/api/receipts**", (route) =>
      route.fulfill(json({ receipts: [], total: 0 }))
    );
    await page.goto("/receipts");
    await expect(page.getByText("No receipts yet")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole("link", { name: /Bring a job/ })).toHaveAttribute("href", "/bring");
  });
});
