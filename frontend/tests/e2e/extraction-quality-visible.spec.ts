import { test, expect, type Page } from "@playwright/test";

/**
 * Can the user SEE how well we read their CV?
 *
 * WHY THIS EXISTS. We built a gate that scores every extraction — coverage,
 * precision, completeness — and for a while it only wrote to a log file. That
 * is the shape that has burned this repo twice already: a value computed,
 * stored, and shown to nobody.
 *
 * It matters most here of all. Measured on 7 real CVs, the deterministic pass
 * captured 18-48% of what people had written. The person whose profile it is
 * had no way to know that, and therefore no way to fix it — they just quietly
 * got worse job matches forever.
 *
 * So these specs check the thing a unit test cannot: that the warning reaches
 * a real browser, in words a person understands, and that it STAYS QUIET when
 * extraction went well. A banner that appears every time is a banner nobody
 * reads.
 *
 * Hermetic, same pattern as the other specs: fake the session cookie
 * (middleware only checks presence) and mock the API.
 */

const SESSION_COOKIE = {
  name: "job360_session",
  value: "e2e-token",
  domain: "localhost",
  path: "/",
};

const RAW_CV = `Jane Okafor
Registered Nurse
SUMMARY
Senior nurse with ICU experience across NHS trusts.
EXPERIENCE
Staff Nurse, Royal London Hospital
Delivered care using ALS and BLS protocols and Epic charting.`;

function profilePayload(score: Record<string, unknown> | undefined, skills: string[]) {
  return {
    summary: {
      is_complete: true,
      job_titles: ["Registered Nurse"],
      skills_count: skills.length,
      cv_length: RAW_CV.length,
      has_linkedin: false,
      has_github: false,
      education: [],
      experience_level: "senior",
    },
    preferences: { target_job_titles: ["Nurse"], additional_skills: [] },
    cv_detail: {
      raw_text: RAW_CV,
      skills,
      job_titles: ["Registered Nurse"],
      companies: [],
      education: [],
      certifications: [],
      summary_text: "Senior nurse with ICU experience.",
      experience_text: "",
      name: "Jane Okafor",
      headline: "Registered Nurse",
      location: "London",
      achievements: [],
      highlights: skills,
      ...(score ? { extraction_score: score } : {}),
    },
    skill_tiers: {},
  };
}

async function mockProfile(
  page: Page,
  score: Record<string, unknown> | undefined,
  skills: string[]
) {
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
      body: JSON.stringify(profilePayload(score, skills)),
    })
  );
}

test.describe("Extraction quality is visible to the user", () => {
  test("a weak extraction warns the person, in their own words", async ({
    page,
    context,
  }) => {
    await context.addCookies([SESSION_COOKIE]);
    await mockProfile(
      page,
      {
        verdict: "weak",
        overall: 0.55,
        coverage: 0.18,
        precision: 0.95,
        problems: [
          "only 18% of the document's own specific terms reached the profile — most of what this person wrote was not understood",
          "no job titles — search has no role to aim at",
        ],
      },
      ["ALS", "BLS"]
    );

    await page.goto("/profile");

    const warning = page.getByTestId("extraction-quality-warning");
    await expect(warning).toBeVisible({ timeout: 30_000 });

    // The number must be in PLAIN words. "coverage 0.18" means nothing to the
    // person whose CV it is; "about 18%" does.
    await expect(warning).toContainText("18%");
    await expect(warning).toContainText(/understood/i);
    // And it must say what it COSTS them, not just that a number is low.
    await expect(warning).toContainText(/will not be matched/i);
    // The specific problems are shown so the warning is actionable.
    await expect(warning).toContainText(/no job titles/i);
  });

  test("a good extraction says nothing at all", async ({ page, context }) => {
    await context.addCookies([SESSION_COOKIE]);
    await mockProfile(
      page,
      { verdict: "good", overall: 0.82, coverage: 0.71, precision: 1.0, problems: [] },
      ["ALS", "BLS", "Epic", "NEWS2"]
    );

    await page.goto("/profile");
    // Wait for the page to actually render before asserting an ABSENCE —
    // asserting "not visible" on a blank page passes for the wrong reason.
    await expect(page.getByText(/CV Uploaded/i)).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("extraction-quality-warning")).toHaveCount(0);
  });

  test("a profile saved before the gate existed does not break the page", async ({
    page,
    context,
  }) => {
    await context.addCookies([SESSION_COOKIE]);
    // No extraction_score key at all — every profile stored before this shipped.
    await mockProfile(page, undefined, ["ALS", "BLS"]);

    await page.goto("/profile");
    await expect(page.getByText(/CV Uploaded/i)).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("extraction-quality-warning")).toHaveCount(0);
  });

  test("the CV panel confirms the upload was parsed", async ({ page, context }) => {
    await context.addCookies([SESSION_COOKIE]);
    await mockProfile(
      page,
      { verdict: "good", overall: 0.8, coverage: 0.7, precision: 1.0, problems: [] },
      ["ALS", "BLS", "Epic"]
    );

    await page.goto("/profile");
    await expect(page.getByText(/CV Uploaded/i)).toBeVisible({ timeout: 30_000 });
  });
});
