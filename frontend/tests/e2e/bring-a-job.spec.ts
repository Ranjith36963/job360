import { test, expect } from "@playwright/test";

/**
 * Bring a job + application receipt — the slice-one journey
 * (docs/plans/2026-09-02-bring-a-job/spec.md, R1–R7).
 *
 * 1. Anonymous /bring and /receipts redirect to /login (middleware).
 * 2. Paste an ad on /bring → land on the job page with the ad text shown.
 * 3. Click "I applied" → a receipt is created and linked.
 * 4. /receipts lists it; /receipts/{id} shows the frozen CV, letter and ad.
 *
 * The backend is mocked with page.route — this proves the UI wiring, not the
 * database. The Postgres truth is backend/tests/test_bring_a_job.py and
 * test_receipts.py.
 */

const AD_TEXT =
  "We are hiring a Senior Python Engineer to build our matching engine.\n\nYou will own FastAPI services and Postgres.";

// Mirrors backend/src/api/models.py JobResponse field-for-field. The page
// reads the defaulted lists unguarded (they are never missing from a real
// response), so a partial mock crashes the page with "reading 'map'".
const BROUGHT_JOB = {
  id: 9001,
  title: "Senior Python Engineer",
  company: "Acme Ltd",
  location: "London, UK",
  salary: null,
  match_score: 74,
  source: "user_brought",
  date_found: new Date().toISOString(),
  apply_url: "https://careers.example.com/jobs/123",
  visa_flag: false,
  visa_status: "unknown",
  job_type: "",
  experience_level: "",
  role: 30,
  skill: 25,
  location_score: 8,
  recency: 10,
  seniority_score: 8,
  salary_score: 0,
  visa_score: 0,
  workplace_score: 0,
  dims_active: false,
  experience: 0,
  credentials: 0,
  semantic: 0,
  penalty: 0,
  matched_skills: ["Python", "FastAPI", "Postgres"],
  missing_required: [],
  transferable_skills: [],
  action: null as string | null,
  bucket: "strong",
  description: AD_TEXT,
  posted_at: null,
  first_seen_at: new Date().toISOString(),
  last_seen_at: new Date().toISOString(),
  date_confidence: null,
  staleness_state: "active",
  title_canonical: "Python Engineer",
  seniority: "senior",
  employment_type: null,
  workplace_type: null,
  visa_sponsorship: null,
  salary_min_gbp: null,
  salary_max_gbp: null,
  salary_period: null,
  salary_currency_original: null,
  required_skills: ["Python", "FastAPI"],
  nice_to_have_skills: null,
  industry: null,
  years_experience_min: null,
  dedup_group_ids: null,
  llm_fit_score: null,
  llm_verdict: null,
  llm_reason: null,
  deadline: null,
  deadline_source: null,
};

const RECEIPT = {
  id: 501,
  user_id: "test-user-id",
  job_id: BROUGHT_JOB.id,
  sent_at: new Date().toISOString(),
  channel: "web",
  note: "",
  job_title: BROUGHT_JOB.title,
  job_company: BROUGHT_JOB.company,
  job_location: BROUGHT_JOB.location,
  job_apply_url: BROUGHT_JOB.apply_url,
  job_description: AD_TEXT,
  cv_text: "RANJITH — Senior Python Engineer\nFastAPI, Postgres, asyncio.",
  cv_origin: "polished",
  cover_letter_text: null,
  cover_letter_origin: null,
  profile_version: 3,
};

const RECEIPT_SUMMARY = {
  id: RECEIPT.id,
  job_id: RECEIPT.job_id,
  sent_at: RECEIPT.sent_at,
  channel: RECEIPT.channel,
  note: RECEIPT.note,
  job_title: RECEIPT.job_title,
  job_company: RECEIPT.job_company,
  job_location: RECEIPT.job_location,
  has_cv: true,
  has_cover_letter: false,
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

  test("paste an ad → job page → I applied → receipt shows what was sent", async ({
    page,
    context,
  }) => {
    await context.addCookies([SESSION_COOKIE]);

    // Playwright: the most recently added route wins, so register the general
    // patterns first and the specific ones after.
    await page.route("**/api/status**", (route) =>
      route.fulfill(json({ jobs_total: 1, last_run: null, sources: [] }))
    );
    await page.route("**/api/pipeline**", (route) => route.fulfill(json({ applications: [] })));

    let bringBody: Record<string, unknown> | null = null;
    await page.route("**/api/jobs/bring", (route) => {
      bringBody = route.request().postDataJSON() as Record<string, unknown>;
      return route.fulfill(json({ job: BROUGHT_JOB, existing: false, scored: true }));
    });
    await page.route(`**/api/jobs/${BROUGHT_JOB.id}`, (route) => route.fulfill(json(BROUGHT_JOB)));
    // DedupGroupViewer maps over `duplicates` as soon as it lands — a bare {}
    // here crashes the whole page, not just the widget.
    await page.route(`**/api/jobs/${BROUGHT_JOB.id}/duplicates`, (route) =>
      route.fulfill(json({ job_id: BROUGHT_JOB.id, duplicates: [], total: 0 }))
    );

    let receiptPosted = false;
    await page.route(`**/api/receipts/${BROUGHT_JOB.id}`, (route) => {
      if (route.request().method() === "POST") {
        receiptPosted = true;
        return route.fulfill(json(RECEIPT, 201));
      }
      return route.continue();
    });
    await page.route(`**/api/receipts/${RECEIPT.id}`, (route) => route.fulfill(json(RECEIPT)));
    await page.route("**/api/receipts?**", (route) =>
      route.fulfill(json({ receipts: [RECEIPT_SUMMARY], total: 1 }))
    );
    await page.route("**/api/receipts", (route) =>
      route.fulfill(json({ receipts: [RECEIPT_SUMMARY], total: 1 }))
    );

    // Dev-mode warm-up: `next dev` compiles /jobs/[id] and /receipts/[id] on
    // their first hit (measured 25s cold on 2026-09-02). The App Router only
    // changes the URL once the new page's payload arrives, so a cold compile
    // looks exactly like "router.push never happened". Visit both once so the
    // timed assertions below measure the product, not the compiler. CI runs a
    // prebuilt server, where this costs two fast navigations.
    await page.goto(`/jobs/${BROUGHT_JOB.id}`);
    await page.goto(`/receipts/${RECEIPT.id}`);

    // --- 1. Paste the ad -------------------------------------------------
    await page.goto("/bring");
    await expect(page).not.toHaveURL(/\/login/);
    await expect(page.getByRole("heading", { name: "Bring a job" })).toBeVisible();

    const scoreBtn = page.getByRole("button", { name: /score this job/i });
    await expect(scoreBtn).toBeDisabled(); // R1: title, company, ad are required

    await page.getByLabel("Job title").fill(BROUGHT_JOB.title);
    await page.getByLabel("Company").fill(BROUGHT_JOB.company);
    await page.getByLabel(/^Location/).fill(BROUGHT_JOB.location);
    await page.getByLabel(/Link to the ad/).fill(BROUGHT_JOB.apply_url);
    await page.getByLabel("The ad, as written").fill(AD_TEXT);
    await expect(scoreBtn).toBeEnabled();
    await scoreBtn.click();

    // --- 2. Land on the job page with the ad text ------------------------
    await expect(page).toHaveURL(new RegExp(`/jobs/${BROUGHT_JOB.id}$`), { timeout: 10_000 });
    expect(bringBody).not.toBeNull();
    expect(bringBody!.title).toBe(BROUGHT_JOB.title);
    expect(bringBody!.description).toBe(AD_TEXT);

    await expect(page.getByTestId("brought-description")).toContainText(
      "Senior Python Engineer to build our matching engine",
      { timeout: 10_000 }
    );

    // --- 3. I applied → receipt ------------------------------------------
    const appliedBtn = page.getByRole("button", { name: /I applied/ });
    await expect(appliedBtn).toBeVisible();
    await appliedBtn.click();
    await expect(page.getByText(/Receipt kept for Acme Ltd/)).toBeVisible({ timeout: 10_000 });
    expect(receiptPosted).toBe(true);

    // The button never becomes a toggle — a second click is a re-application.
    await expect(page.getByRole("button", { name: /Applied again/ })).toBeVisible();

    const receiptLink = page.getByRole("link", { name: /View the receipt/ });
    await expect(receiptLink).toHaveAttribute("href", `/receipts/${RECEIPT.id}`);
    await receiptLink.click();

    // --- 4. The receipt is frozen: CV, letter, ad ------------------------
    await expect(page).toHaveURL(new RegExp(`/receipts/${RECEIPT.id}$`), { timeout: 10_000 });
    await expect(page.getByTestId("receipt-title")).toHaveText(BROUGHT_JOB.title);
    await expect(page.getByTestId("receipt-cv")).toContainText("FastAPI, Postgres, asyncio");
    await expect(page.getByTestId("receipt-cv")).toContainText("your edited version");
    await expect(page.getByTestId("receipt-cover-letter")).toContainText(
      "Nothing was tailored in Job360 for this one"
    );
    await expect(page.getByTestId("receipt-ad")).toContainText("build our matching engine");
    await expect(page.getByText("profile v3")).toBeVisible();

    // No edit or delete anywhere on a receipt (R6: append-only).
    await expect(page.getByRole("button", { name: /delete|edit|remove/i })).toHaveCount(0);

    // --- 5. The list page shows it too -----------------------------------
    await page.goto("/receipts");
    await expect(page.getByTestId("receipts-list")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("Acme Ltd")).toBeVisible();
    await expect(page.getByText("CV kept")).toBeVisible();
    await expect(page.getByText("No cover letter")).toBeVisible();
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
