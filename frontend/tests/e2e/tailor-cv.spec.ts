import { test, expect } from "@playwright/test";

/**
 * Per-User AI CV & Cover Letter E2E smoke (docs/product/peruser_cv_coverletter.md §3.5).
 * Fakes the session cookie and mocks every `/api/tailor/**` call — checks that
 * the job page's "Tailor my CV" button opens the panel, shows the generated
 * CV + cover letter, lets you edit + save, and offers a PDF download.
 */

const MOCK_JOB = {
  id: 1,
  title: "Senior ML Engineer",
  company: "DeepTech Ltd",
  location: "London, UK",
  match_score: 87,
  date_found: new Date().toISOString(),
  source: "adzuna",
  apply_url: "https://example.com/apply",
  action: null,
  salary: null,
  matched_skills: [],
  missing_required: [],
  transferable_skills: [],
  role: 10,
  skill: 10,
  seniority_score: 5,
  experience: 5,
  credentials: 3,
  location_score: 5,
  recency: 5,
  semantic: 5,
  penalty: 0,
};

const MOCK_BUNDLE = {
  job_id: 1,
  documents: [
    {
      doc_kind: "cv",
      ai_draft: "Generated CV draft tailored for Senior ML Engineer.",
      polished: null,
      status: "draft",
      model: "cerebras",
      updated_at: null,
    },
    {
      doc_kind: "cover_letter",
      ai_draft: "Generated cover letter draft for DeepTech Ltd.",
      polished: null,
      status: "draft",
      model: "cerebras",
      updated_at: null,
    },
  ],
  quota_used: 1,
  quota_limit: 10,
};

test.describe("Tailor my CV", () => {
  test.beforeEach(async ({ context }) => {
    await context.addCookies([
      {
        name: "job360_session",
        value: "smoke-test-token",
        domain: "localhost",
        path: "/",
      },
    ]);
  });

  // FIXME(e2e): STALE spec — the job-detail page renders tailor as an INLINE
  // <TailorSection> (JobDetailClient.tsx:494), not a "Tailor my CV" button that
  // opens a dialog (that button+dialog lives on the dashboard JobCard). So the
  // button click times out and the dialog assertions can't pass here. Needs a
  // rewrite to target the inline section (or to run against the dashboard card),
  // not an auth/env issue. Quarantined until rewritten.
  test.fixme("opens from the job page, shows generated docs, edits + saves, offers download", async ({
    page,
  }) => {
    await page.route("**/api/jobs/**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_JOB),
      })
    );

    await page.route("**/api/tailor/**", (route) => {
      const req = route.request();
      if (req.method() === "GET") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(MOCK_BUNDLE),
        });
      }
      if (req.method() === "PATCH") {
        const body = req.postDataJSON() as { text: string };
        const kind = req.url().includes("/cover_letter") ? "cover_letter" : "cv";
        const seed = MOCK_BUNDLE.documents.find((d) => d.doc_kind === kind);
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            doc_kind: kind,
            ai_draft: seed?.ai_draft ?? "",
            polished: body.text,
            status: "draft",
            model: "cerebras",
            updated_at: new Date().toISOString(),
          }),
        });
      }
      return route.continue();
    });

    await page.goto("/jobs/1");
    await expect(page).not.toHaveURL(/\/login/);

    // Open the tailor panel from the job detail page's action rail
    await page.getByRole("button", { name: /tailor my cv/i }).first().click();

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();

    // CV tab is active by default — its generated draft is shown
    await expect(dialog.getByText(/generated cv draft/i)).toBeVisible({
      timeout: 30_000,
    });

    // Cover Letter tab shows its own generated draft
    await dialog.getByRole("tab", { name: /cover letter/i }).click();
    await expect(dialog.getByText(/generated cover letter draft/i)).toBeVisible();

    // Back to CV — edit the draft into a polished version and save it
    await dialog.getByRole("tab", { name: /^cv$/i }).click();
    const textarea = dialog.locator("textarea");
    await textarea.fill("My polished, hand-edited CV text.");
    await dialog.getByRole("button", { name: /^save$/i }).click();
    await expect(textarea).toHaveValue("My polished, hand-edited CV text.");

    // Always-editable + always-downloadable guardrails (spec §4.3/§4.5)
    await expect(dialog.getByRole("button", { name: /download pdf/i })).toBeVisible();

    // Guardrail #4 — we don't auto-apply, the user submits on the company site
    await expect(dialog.getByText(/apply opens the company site/i)).toBeVisible();
  });
});
