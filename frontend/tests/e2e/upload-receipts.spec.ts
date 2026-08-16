import { test, expect, type Page } from "@playwright/test";

/**
 * Can the user SEE what we hold, and when we got it?
 *
 * WHY THIS EXISTS. After an upload the only feedback was a small tick in the
 * card header. The owner's words on a live smoke test: "I get a neon colour
 * tick mark but I didn't catch that" — and nothing anywhere NAMED the file, so
 * he could not tell which CV was on file or whether a re-upload had actually
 * replaced it. GitHub had no confirmation at all.
 *
 * The backend tests prove the API RETURNS the filename. They cannot prove the
 * page RENDERS it — and a value stored, returned, and shown to nobody is the
 * exact failure shape this repo has hit before. So this spec drives a real
 * browser and reads the receipt with the same eyes the user does.
 *
 * Hermetic: fake the session cookie (middleware only checks presence) and mock
 * the API, same as the other specs.
 */

const SESSION_COOKIE = {
  name: "job360_session",
  value: "e2e-token",
  domain: "localhost",
  path: "/",
};

const RAW_CV = `Ada Lovelace
Machine Learning Engineer
SUMMARY
Builds production ML systems.`;

/** A profile whose three inputs all carry a receipt. */
function profileWithReceipts(over: Record<string, unknown> = {}) {
  return {
    summary: {
      is_complete: true,
      job_titles: ["ML Engineer"],
      skills_count: 2,
      cv_length: RAW_CV.length,
      has_linkedin: true,
      has_github: true,
      education: [],
      experience_level: "senior",
      cv_filename: "Ada_Lovelace_CV_2026.pdf",
      cv_uploaded_at: "2026-08-08T10:30:00+00:00",
      linkedin_filename: "Profile_Ada.pdf",
      linkedin_uploaded_at: "2026-08-07T09:00:00+00:00",
      github_username: "adalovelace",
      github_connected_at: "2026-08-06T08:00:00+00:00",
      github_repo_count: 14,
      ...over,
    },
    preferences: { target_job_titles: [], additional_skills: [] },
    cv_detail: {
      raw_text: RAW_CV,
      skills: ["Python", "PyTorch"],
      job_titles: ["ML Engineer"],
      companies: [],
      education: [],
      certifications: [],
      summary_text: "Builds production ML systems.",
      experience_text: "",
      name: "Ada Lovelace",
      headline: "Machine Learning Engineer",
      location: "London",
      achievements: [],
      highlights: ["Python", "PyTorch"],
    },
    skill_tiers: {},
  };
}

async function mockProfile(page: Page, payload: unknown) {
  await page.route("**/api/auth/me**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ id: "e2e-user", email: "e2e@example.com" }),
    })
  );
  await page.route("**/api/profile**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(payload),
    })
  );
}

test.describe("Upload receipts — what we hold, and when", () => {
  test("names the actual CV, LinkedIn file and GitHub handle on screen", async ({
    page,
    context,
  }) => {
    await context.addCookies([SESSION_COOKIE]);
    await mockProfile(page, profileWithReceipts());
    await page.goto("/profile");

    const receipts = page.getByTestId("upload-receipt");
    await expect(receipts.first()).toBeVisible({ timeout: 30_000 });
    // One per input: CV, LinkedIn, GitHub.
    await expect(receipts).toHaveCount(3);

    // The CV receipt names the REAL file — this is the whole point. A generic
    // "uploaded" tick is what we are replacing.
    await expect(receipts.nth(0)).toContainText("Ada_Lovelace_CV_2026.pdf");
    await expect(receipts.nth(0)).toContainText(/added/i);
    await expect(receipts.nth(0)).toContainText("2026");

    await expect(receipts.nth(1)).toContainText("Profile_Ada.pdf");

    // GitHub has no file, so the handle is the receipt and the repo count is
    // the proof of what was actually read.
    await expect(receipts.nth(2)).toContainText("@adalovelace");
    await expect(receipts.nth(2)).toContainText("14 public repos read");
  });

  test("an older profile with no stored receipt still shows a sane state", async ({
    page,
    context,
  }) => {
    // Rows saved before the receipt fields existed load with empty strings.
    // They must degrade to a plain "on file", never render "undefined" or a
    // blank chip.
    await context.addCookies([SESSION_COOKIE]);
    await mockProfile(
      page,
      profileWithReceipts({
        cv_filename: "",
        cv_uploaded_at: "",
        linkedin_filename: "",
        linkedin_uploaded_at: "",
        github_username: "",
        github_connected_at: "",
        github_repo_count: 0,
      })
    );
    await page.goto("/profile");

    const receipts = page.getByTestId("upload-receipt");
    await expect(receipts.first()).toBeVisible({ timeout: 30_000 });
    await expect(receipts.nth(0)).toContainText("CV on file");
    await expect(receipts.nth(1)).toContainText("LinkedIn profile on file");
    await expect(receipts.nth(2)).toContainText("GitHub connected");
    for (const t of ["undefined", "NaN", "Invalid Date"]) {
      await expect(page.getByTestId("upload-receipt").first()).not.toContainText(t);
    }
  });

  test("the full CV text appears exactly ONCE on the page", async ({
    page,
    context,
  }) => {
    // It used to render in the CV card AND in CVViewer's "Full CV Text" panel.
    // The panel was removed; this guards the duplicate from returning.
    await context.addCookies([SESSION_COOKIE]);
    await mockProfile(page, profileWithReceipts());
    await page.goto("/profile");

    await expect(page.getByText("What we extracted from your profile")).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByText("Full CV Text")).toHaveCount(0);
    await expect(page.getByText("Builds production ML systems.").first()).toBeVisible();
  });
});
