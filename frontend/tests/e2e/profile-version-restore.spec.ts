import { test, expect } from "@playwright/test";

/**
 * Profile page E2E smoke — checks that:
 * 1. Unauthenticated visit to /profile redirects to /login (middleware).
 * 2. Authenticated visit with mocked profile API shows profile content and
 *    the Version History button that opens the drawer.
 */

const MOCK_PROFILE = {
  cv_data: {
    name: "Test User",
    skills: ["Python", "FastAPI"],
    job_titles: ["Software Engineer"],
    years_experience: 5,
    education: [],
    achievements: [],
    highlights: [],
    companies: [],
    certifications: [],
    languages: [],
    linkedin_positions: [],
    linkedin_skills: [],
    github_languages: {},
    github_topics: [],
    github_skills_inferred: [],
  },
  preferences: {
    target_roles: ["Backend Engineer"],
    locations: ["London"],
    salary_min: 60000,
    salary_max: 100000,
    employment_types: ["full-time"],
    visa_required: false,
    github_username: null,
  },
  skill_tiers: { primary: ["Python"], secondary: ["FastAPI"], tertiary: [] },
  skill_esco: [],
  skill_provenance: {},
  linkedin_subsections: {},
  github_temporal: null,
  current_version_id: 1,
};

const MOCK_VERSIONS = {
  versions: [
    {
      id: 1,
      created_at: new Date().toISOString(),
      summary: "Initial CV upload",
      skill_count: 2,
      version_number: 1,
    },
  ],
  total: 1,
};

test.describe("Profile page", () => {
  test("anonymous visit redirects to /login", async ({ page }) => {
    await page.goto("/profile");
    await expect(page).toHaveURL(/\/login/);
  });

  test("authenticated visit shows profile heading and version history button", async ({
    page,
    context,
  }) => {
    await context.addCookies([
      {
        name: "job360_session",
        value: "smoke-test-token",
        domain: "localhost",
        path: "/",
      },
    ]);

    await page.route("**/api/profile/versions**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_VERSIONS),
      })
    );

    await page.route("**/api/profile**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_PROFILE),
      })
    );

    await page.goto("/profile");
    await expect(page).not.toHaveURL(/\/login/);

    // Profile heading
    await expect(page.getByRole("heading", { name: /profile/i })).toBeVisible({
      timeout: 10_000,
    });

    // Version history button — the profile page labels it "History" (opens the
    // VersionHistoryDrawer). Match /history/i, not the stale /version history/i.
    await expect(page.getByRole("button", { name: /history/i })).toBeVisible();
  });
});
