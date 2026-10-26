import { test, expect } from "@playwright/test";

/**
 * Profile page — the agent-edit provenance mark (slice 4, spec R11,
 * docs/plans/2026-09-05-contacts-stats/spec.md) and `cv_data.links` (R9).
 *
 * `GET /profile` returns `agent_edits`: the current overlay an agent has set
 * via `PATCH /profile`. The page renders the (already-merged) value in place
 * plus a small "Changed by <name> · was <previous>" mark with a "Take back"
 * button next to it — ONLY for paths that carry a live ASSISTANT edit
 * (owner decision 2026-09-25; the human's own web saves never get a mark). Mocked with page.route, same
 * hermetic style as tests/e2e/profile-version-restore.spec.ts.
 */

const MOCK_USER = { id: "e2e-user", email: "e2e@example.com" };

const AGENT_EDITS = [
  {
    path: "cv_data.location",
    value: "London, UK",
    previous_value: "Leeds",
    set_by: "agent:cli",
    set_at: "2026-09-01T10:00:00Z",
  },
  {
    // A field the CV tab still shows (work arrangement moved to Memory, 2026-10-26).
    path: "preferences.about_me",
    value: "Data engineer",
    previous_value: "",
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
    work_arrangement: "",
    experience_level: "",
    negative_keywords: [],
    // The overlaid value — the page shows the already-merged value; the mark
    // is what tells the seeker an agent (not them) set it.
    about_me: "Data engineer",
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
    // Overlaid value, same reasoning as about_me above.
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
    await expect(locationRow).toContainText("Changed by cli · was Leeds");

    // cv_data.name carries NO edit — its heading shows no mark, even though
    // it sits right next to the location row that does.
    const nameHeading = page.getByRole("heading", { name: "Test User" });
    await expect(nameHeading).toBeVisible();
    await expect(nameHeading).not.toContainText("Changed by");

    // preferences.about_me carries an edit — its label shows the mark.
    const aboutMeLabel = page.locator("label", { hasText: "About Me" });
    await expect(aboutMeLabel).toContainText("Changed by cli · was empty");

    // preferences.industries carries NO edit — no mark on its label.
    const industriesLabel = page.locator("label", { hasText: "Industries" });
    await expect(industriesLabel).toBeVisible();
    await expect(industriesLabel).not.toContainText("Changed by");

    // Exactly two marks on the whole page — one per edited path, no more.
    await expect(page.getByTestId("agent-edit-mark")).toHaveCount(2);
  });

  test("Take back sends the path and the mark goes; History lists both authors", async ({
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
    // Registered AFTER the broad pattern: Playwright checks routes newest-first.
    let takenBack: unknown = null;
    await page.route("**/api/profile/edits/take-back", async (route) => {
      takenBack = route.request().postDataJSON();
      const after = {
        ...MOCK_PROFILE,
        preferences: { ...MOCK_PROFILE.preferences, about_me: "" },
        agent_edits: AGENT_EDITS.filter((e) => e.path !== "preferences.about_me"),
      };
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(after) });
    });
    // ONE combined history for the whole preferences section (owner
    // decision, 2026-09-27) — FieldHistory fetches every preference path,
    // so only `preferences.about_me` returns real rows here; every
    // other path answers empty, keeping the merged list to exactly these 2.
    await page.route("**/api/profile/edits/history**", (route) => {
      const url = new URL(route.request().url());
      const path = url.searchParams.get("path");
      const rows =
        path === "preferences.about_me"
          ? [
              { value: "Data engineer", set_by: "agent:cli", set_at: "2026-09-02T11:00:00Z" },
              { value: "ML engineer", set_by: "web", set_at: "2026-09-01T09:00:00Z" },
            ]
          : [];
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ path, rows }),
      });
    });

    await page.goto("/profile");
    await expect(
      page.getByRole("heading", { level: 1, name: "Profile", exact: true })
    ).toBeVisible({ timeout: 10_000 });

    // The header counts the one preference the assistant set.
    await expect(page.getByRole("link", { name: "1 preference set by your assistant" })).toBeVisible();

    // History: one combined inline list for the whole section, newest first,
    // both authors.
    await page.getByRole("button", { name: "History of Your preferences" }).click();
    const rows = page.getByTestId("field-history-row");
    await expect(rows).toHaveCount(2);
    await expect(rows.nth(0)).toContainText("cli");
    await expect(rows.nth(0)).toContainText("Data engineer");
    await expect(rows.nth(1)).toContainText("You");
    await expect(rows.nth(1)).toContainText("ML engineer");

    // Take back the About me change.
    const aboutMeLabel = page.locator("label", { hasText: "About Me" });
    await aboutMeLabel.getByRole("button", { name: "Take back cli's change" }).click();
    await expect.poll(() => takenBack).toEqual({ path: "preferences.about_me" });
    await expect(aboutMeLabel).not.toContainText("Changed by");
    await expect(page.getByTestId("agent-edit-mark")).toHaveCount(1);
    await expect(page.getByText(/set by your assistant/)).toHaveCount(0);
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
