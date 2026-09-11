import { test, expect, type Page } from "@playwright/test";

/**
 * Slice 9 (#516) — "Flag for next time": lessons written on the application
 * page, read back on the profile page. Hermetic (frontend-only): fake the
 * session cookie and mock every API call with `page.route`, same pattern as
 * tests/e2e/applications-detail-diff.spec.ts and
 * tests/e2e/profile-agent-edits.spec.ts.
 */

const SESSION_COOKIE = {
  name: "job360_session",
  value: "e2e-token",
  domain: "localhost",
  path: "/",
};

const MOCK_USER = { id: "e2e-user", email: "e2e@example.com" };

const APPLICATION_ID = 8282;
const JOB_ID = 556;

function applicationDetail() {
  return {
    id: APPLICATION_ID,
    job_id: JOB_ID,
    status: "considering",
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    last_event_at: "2026-09-01T00:00:00Z",
    job: {
      job_title: "Platform Engineer",
      job_company: "Northwind",
      job_location: "Remote",
      job_url: "https://northwind.example/careers/9",
      job_source: "user_brought",
      job_description_snapshot: "Build the platform. Kubernetes, Go, Postgres.",
      snapshot_at: "2026-09-01T00:00:00Z",
      catalog_present: true,
    },
    fit: null,
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

// Copied from tests/e2e/profile-agent-edits.spec.ts's MOCK_PROFILE — the
// minimum ProfileResponse shape the profile page needs to render without
// crashing (see src/app/profile/page.tsx).
function mockProfile() {
  return {
    summary: {
      is_complete: true,
      job_titles: ["Software Engineer"],
      skills_count: 1,
      cv_length: 500,
      has_linkedin: false,
      has_github: false,
      education: [],
      experience_level: "mid",
      cv_filename: "cv.pdf",
      cv_uploaded_at: new Date().toISOString(),
      linkedin_filename: "",
      linkedin_uploaded_at: "",
      github_username: "",
      github_connected_at: "",
      github_repo_count: 0,
    },
    preferences: {
      target_job_titles: ["Backend Engineer"],
      additional_skills: [],
      excluded_skills: [],
      preferred_locations: [],
      industries: [],
      salary_min: null,
      salary_max: null,
      work_arrangement: "any",
      experience_level: "",
      negative_keywords: [],
      about_me: "",
      needs_visa: false,
    },
    cv_detail: {
      raw_text: "",
      skills: ["Python"],
      job_titles: ["Software Engineer"],
      companies: [],
      education: [],
      certifications: [],
      summary_text: "",
      experience_text: "",
      name: "Test User",
      headline: "",
      location: "",
      achievements: [],
      links: [],
      cv_positions: [],
      cv_projects: [],
      cv_experience_level: "",
      cv_right_to_work: "",
      cv_industries: [],
      highlights: [],
      extraction_score: {},
    },
    skill_tiers: {},
    skill_esco: {},
    skill_provenance: {},
    skills_by_source: {},
    ai_suggestions: [],
    linkedin_subsections: {},
    github_temporal: {},
    github_detail: {},
    current_version_id: null,
    search_titles: [],
    agent_edits: [],
  };
}

function mockLessons() {
  return {
    lessons: [
      {
        event_id: 1,
        application_id: 8282,
        job_title: "Platform Engineer",
        job_company: "Northwind",
        detail: "Ask about sponsorship first.",
        occurred_at: "2026-09-10T00:00:00Z",
        recorded_by: "user",
      },
    ],
    total: 1,
  };
}

async function mockAuth(page: Page) {
  await page.route("**/api/auth/me**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_USER) })
  );
}

test.describe("Flag for next time (slice 9)", () => {
  test("application page records a lesson event", async ({ page, context }) => {
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

    let capturedBody: unknown = null;
    await page.route(`**/api/applications/${APPLICATION_ID}/events`, (route) => {
      capturedBody = route.request().postDataJSON();
      route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          event_id: 77,
          application_id: APPLICATION_ID,
          event_type: "lesson",
          status: null,
          status_changed: false,
        }),
      });
    });

    await page.goto(`/applications/${APPLICATION_ID}`);
    await expect(page.getByText("Platform Engineer")).toBeVisible({ timeout: 20_000 });

    await page.getByTestId("lesson-input").fill("Always mention the Kubernetes cert.");
    await page.getByTestId("lesson-submit").click();

    await expect.poll(() => capturedBody).toEqual({
      event_type: "lesson",
      detail: "Always mention the Kubernetes cert.",
    });
  });

  test("profile page lists a mocked lesson linking to its application", async ({ page, context }) => {
    await context.addCookies([SESSION_COOKIE]);
    await mockAuth(page);

    await page.route("**/api/profile**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(mockProfile()) })
    );
    await page.route("**/api/applications/lessons**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(mockLessons()) })
    );

    await page.goto("/profile");
    await expect(
      page.getByRole("heading", { level: 1, name: "Profile", exact: true })
    ).toBeVisible({ timeout: 10_000 });

    const items = page.getByTestId("lesson-item");
    await expect(items).toHaveCount(1);
    await expect(items.first()).toContainText("Ask about sponsorship first.");

    const link = page.getByTestId("lesson-link");
    await expect(link).toHaveAttribute("href", "/applications/8282");
  });
});
