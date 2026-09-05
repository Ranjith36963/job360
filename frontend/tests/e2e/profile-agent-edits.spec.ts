import { test, expect } from "@playwright/test";

/**
 * Profile page — the agent-edit provenance mark (slice 4, spec R11,
 * docs/plans/2026-09-05-contacts-stats/spec.md) and `cv_data.links` (R9).
 *
 * `GET /profile` returns `agent_edits`: the current overlay an agent has set
 * via `PATCH /profile`. The page renders the (already-merged) value in place
 * plus a small "Edited by <set_by> on <date>" mark next to it — ONLY for
 * paths that actually carry an active edit. Mocked with page.route, same
 * hermetic style as tests/e2e/profile-version-restore.spec.ts.
 */

const MOCK_USER = { id: "e2e-user", email: "e2e@example.com" };

const AGENT_EDITS = [
  {
    path: "cv_data.location",
    value: "London, UK",
    set_by: "agent:cli",
    set_at: "2026-09-01T10:00:00Z",
  },
  {
    path: "preferences.work_arrangement",
    value: "remote",
    set_by: "agent:cli",
    set_at: "2026-09-02T11:00:00Z",
  },
];

const MOCK_PROFILE = {
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
    // The overlaid value — the page shows the already-merged value; the mark
    // is what tells the seeker an agent (not them) set it.
    work_arrangement: "remote",
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
    // Overlaid value, same reasoning as work_arrangement above.
    location: "London, UK",
    achievements: [],
    // R9's new field — one https:// link (renders as an anchor) and one
    // non-https value (renders as text, same S5 rule contacts use).
    links: ["https://example.dev/portfolio", "javascript:alert(1)"],
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
  agent_edits: AGENT_EDITS,
};

test.describe("Profile page — agent-edit provenance mark", () => {
  test("an edited field shows the mark; an unedited field does not", async ({
    page,
    context,
  }) => {
    await context.addCookies([
      { name: "job360_session", value: "smoke-test-token", domain: "localhost", path: "/" },
    ]);
    await page.route("**/api/auth/me**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_USER) })
    );
    await page.route("**/api/profile**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_PROFILE) })
    );

    await page.goto("/profile");
    await expect(
      page.getByRole("heading", { level: 1, name: "Profile", exact: true })
    ).toBeVisible({ timeout: 10_000 });

    // cv_data.location carries an edit — its row shows the mark.
    const locationRow = page.locator("p", { hasText: "London, UK" });
    await expect(locationRow).toContainText("Edited by agent:cli");

    // cv_data.name carries NO edit — its heading shows no mark, even though
    // it sits right next to the location row that does.
    const nameHeading = page.getByRole("heading", { name: "Test User" });
    await expect(nameHeading).toBeVisible();
    await expect(nameHeading).not.toContainText("Edited by");

    // preferences.work_arrangement carries an edit — its label shows the mark.
    const workArrangementLabel = page.locator("label", { hasText: "Work Arrangement" });
    await expect(workArrangementLabel).toContainText("Edited by agent:cli");

    // preferences.target_job_titles carries NO edit — no mark on its label.
    const targetTitlesLabel = page.locator("label", { hasText: "Target Job Titles" });
    await expect(targetTitlesLabel).toBeVisible();
    await expect(targetTitlesLabel).not.toContainText("Edited by");

    // Exactly two marks on the whole page — one per edited path, no more.
    await expect(page.getByTestId("agent-edit-mark")).toHaveCount(2);
  });

  test("cv_data.links renders as a list; only the https:// entry is a link", async ({
    page,
    context,
  }) => {
    await context.addCookies([
      { name: "job360_session", value: "smoke-test-token", domain: "localhost", path: "/" },
    ]);
    await page.route("**/api/auth/me**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_USER) })
    );
    await page.route("**/api/profile**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_PROFILE) })
    );

    await page.goto("/profile");
    await expect(page.getByText("Links (2)")).toBeVisible({ timeout: 10_000 });

    const goodLink = page.getByRole("link", { name: "https://example.dev/portfolio" });
    await expect(goodLink).toHaveAttribute("href", "https://example.dev/portfolio");

    // The javascript: value is on the page as plain text, never as an href.
    await expect(page.getByText("javascript:alert(1)")).toBeVisible();
    await expect(page.locator('a[href="javascript:alert(1)"]')).toHaveCount(0);
  });
});
