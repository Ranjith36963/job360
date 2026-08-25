import { test, expect } from "@playwright/test";

/**
 * Profile page E2E smoke — checks that:
 * 1. Unauthenticated visit to /profile redirects to /login (middleware).
 * 2. Authenticated visit with mocked profile API shows profile content and
 *    the Version History button that opens the drawer.
 */

const MOCK_USER = { id: "e2e-user", email: "e2e@example.com" };

const MOCK_PROFILE = {
  // `summary` is REQUIRED by the real /api/profile response and this fixture
  // never had it. calcCompleteness() reads summary.cv_length, so the page threw
  // "Cannot read properties of undefined (reading 'cv_length')" into its error
  // boundary the moment the mock actually applied. The spec still passed,
  // because the button it asserted on used to render whether or not a profile
  // had loaded — it was passing ON the bug this branch fixes (Export/History
  // are now gated on a profile existing, as ClearButton beside them already
  // was). Giving the fixture the real shape is what makes the assertion mean
  // something.
  summary: {
    is_complete: true,
    job_titles: ["Software Engineer"],
    skills_count: 2,
    cv_length: 1200,
    has_linkedin: false,
    has_github: false,
    education: [],
    experience_level: "mid",
    cv_filename: "test-cv.pdf",
    cv_uploaded_at: new Date().toISOString(),
    linkedin_filename: "",
    linkedin_uploaded_at: "",
    github_username: "",
  },
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

    // The cookie only satisfies the MIDDLEWARE. The app's own auth state comes
    // from /api/auth/me, and without it AuthProvider has no user — the page
    // rendered the signed-out navbar ("Log in" / "Get started") during an
    // "authenticated visit". Mock it so the test means what its name says.
    await page.route("**/api/auth/me**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_USER),
      })
    );

    // Registration order matters: Playwright checks routes in REVERSE order, so
    // the more specific /versions pattern must be registered LAST or the broad
    // /api/profile** below swallows it and the drawer receives a profile body.
    await page.route("**/api/profile**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_PROFILE),
      })
    );

    await page.route("**/api/profile/versions**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_VERSIONS),
      })
    );

    await page.goto("/profile");
    await expect(page).not.toHaveURL(/\/login/);

    // Profile heading — level 1 and exact, because /profile/i also matches the
    // "Enrich Your Profile" h3 further down the page and Playwright's strict
    // mode rejects a locator that resolves to two elements.
    await expect(
      page.getByRole("heading", { level: 1, name: "Profile", exact: true })
    ).toBeVisible({ timeout: 10_000 });

    // Version history button — the profile page labels it "History" (opens the
    // VersionHistoryDrawer). Match /history/i, not the stale /version history/i.
    await expect(page.getByRole("button", { name: /history/i })).toBeVisible();
  });
});
