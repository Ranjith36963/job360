import { test, expect, type BrowserContext, type Page } from "@playwright/test";

/**
 * Auth lifecycle (tester point 3, symptoms S1-S15): the signed-in frame after
 * sign-in, a session that ends mid-visit, auth pages for a signed-in user, and
 * logout. Each title names its symptom.
 *
 * Hermetic, same style as profile-agent-edits.spec.ts: the session cookie is
 * faked with context.addCookies (the middleware's E2E bypass trusts a present
 * cookie) and the whole backend is ONE stateful `page.route("**\/api/**")`
 * double. `backend.authed` is the single switch for "the server still knows
 * this session": while true every endpoint answers; once false every endpoint
 * answers 401, exactly as the real backend does after expiry or revocation.
 *
 * E4 (a prefetched route skips the middleware) only reproduces against a
 * PRODUCTION build, which is how CI runs the suite (`npm run build && npm run
 * start`). `next dev` does not prefetch static routes the same way.
 */

const USER = { id: "e2e-user", email: "e2e@example.com" };

const SESSION_COOKIE = {
  name: "job360_session",
  value: "e2e-token",
  domain: "localhost",
  path: "/",
};

const BANNER = "Your session ended. Sign in again to continue.";

const json = (body: unknown, status = 200) => ({
  status,
  contentType: "application/json",
  body: JSON.stringify(body),
});

const APPLICATION = {
  id: 4242,
  job_id: 777,
  job_title: "Data Engineer",
  job_company: "Northwind",
  job_url: "https://northwind.example/careers/7",
  status: "considering",
  last_event_at: "2026-09-04T00:00:00Z",
  events: 1,
  artifacts: { cv: 1 },
  receipts: 0,
  next_step: { code: "apply", label: "CV ready — apply, then mark it applied" },
};

// Smallest ProfileResponse the profile page renders (see profile-agent-edits.spec.ts).
const PROFILE = {
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
    cv_uploaded_at: "2026-09-01T00:00:00Z",
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

type LogoutMode = "ok" | "network-failure";

type Backend = {
  /** True while the server still honours the session cookie. */
  authed: boolean;
  logout: LogoutMode;
  /** Every /api/ path requested, in order (diagnostics only). */
  seen: string[];
};

/**
 * Install the stateful backend double. Nothing reaches a real server: every
 * /api/** request is fulfilled or aborted here.
 */
async function installBackend(
  page: Page,
  context: BrowserContext,
  initial: { authed: boolean; logout?: LogoutMode }
): Promise<Backend> {
  const backend: Backend = { authed: initial.authed, logout: initial.logout ?? "ok", seen: [] };

  await page.route("**/api/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    const method = req.method();
    backend.seen.push(`${method} ${path}`);

    // Sign-in endpoints work while signed out; they flip the server state and
    // hand out the cookie, like the real Set-Cookie would.
    if (
      method === "POST" &&
      (path === "/api/auth/magic-link/consume" || path === "/api/auth/login")
    ) {
      backend.authed = true;
      await context.addCookies([SESSION_COOKIE]);
      return route.fulfill(json(USER));
    }

    if (method === "POST" && path === "/api/auth/logout") {
      if (backend.logout === "network-failure") return route.abort("failed");
      backend.authed = false;
      await context.clearCookies();
      return route.fulfill({ status: 204 });
    }

    if (!backend.authed) {
      // A dead session has no usable cookie: drop it on the first refusal, so
      // a later page load (the /login the app sends the user to) is not
      // mistaken for signed-in by the E2E bypass, which trusts a present cookie.
      await context.clearCookies();
      return route.fulfill(json({ detail: "authentication required" }, 401));
    }

    if (path === "/api/auth/me") return route.fulfill(json(USER));
    if (path === "/api/profile") return route.fulfill(json(PROFILE));
    if (path === "/api/applications/stats") {
      return route.fulfill(
        json({ overall: { brought: 1, applied: 0, replied: 0, interview: 0 } })
      );
    }
    if (path === "/api/applications") {
      const due = url.searchParams.get("due") === "true";
      return route.fulfill(
        json(due ? { applications: [], total: 0 } : { applications: [APPLICATION], total: 1 })
      );
    }
    if (path === "/api/asks") return route.fulfill(json({ asks: [], open_count: 0 }));
    if (path === "/api/whats-new") {
      return route.fulfill(
        json({
          now: "2026-09-05T00:00:00Z",
          since: "2026-08-29T00:00:00Z",
          events: [],
          applications: [],
          next_since: "2026-09-05T00:00:00Z",
          next_after_id: null,
          truncated: false,
          open_asks: [],
        })
      );
    }
    if (path === "/api/changes") {
      return route.fulfill(
        json({
          cursor: "c1",
          areas: {},
          assistant_last_active_at: null,
        })
      );
    }
    // Anything else a signed-in page may ask for: an empty object keeps the
    // page alive without inventing data.
    return route.fulfill(json({}));
  });

  return backend;
}

/** The signed-in frame: sidebar with a Log out button. */
function sidebarLogout(page: Page) {
  return page.getByTestId("app-sidebar").getByRole("button", { name: /log out/i });
}

/** The signed-out header's sign-in link. */
function headerLogIn(page: Page) {
  return page.getByRole("link", { name: "Log in" }).first();
}

test.describe("sign-in lands in the signed-in frame", () => {
  test("S1: after a magic-link sign-in the signed-in frame shows without a focus event", async ({
    page,
    context,
  }) => {
    await installBackend(page, context, { authed: false });

    await page.goto("/auth/magic?token=e2e-token&next=%2Fapplications");
    await page.getByRole("button", { name: "Sign in to Job360" }).click();

    await expect(page).toHaveURL(/\/applications$/);
    // No window `focus` is dispatched anywhere in this test.
    await expect(sidebarLogout(page)).toBeVisible();
    await expect(headerLogIn(page)).toBeHidden();
  });

  test("S2: after a password sign-in the signed-in frame shows without a focus event", async ({
    page,
    context,
  }) => {
    await installBackend(page, context, { authed: false });

    await page.goto("/login?next=%2Fprofile");
    await page.getByRole("button", { name: "Use password instead" }).click();
    await page.getByLabel("Email").fill(USER.email);
    await page.getByLabel("Password").fill("correct horse battery");
    await page.getByRole("button", { name: "Sign in", exact: true }).click();

    await expect(page).toHaveURL(/\/profile$/);
    await expect(sidebarLogout(page)).toBeVisible();
    await expect(headerLogIn(page)).toBeHidden();
  });
});

// The middleware branch for the auth pages (U6 in the diagnosis). With
// E2E_TEST_MODE a present cookie is trusted, so the gatekeeper redirects
// without a backend. Kept apart so the lead can ship these with the
// middleware PR.
test.describe("gatekeeper (middleware)", () => {
  const cases: Array<{ from: string; to: RegExp }> = [
    { from: "/login?next=%2Fprofile", to: /\/profile$/ },
    { from: "/register?next=%2Fprofile", to: /\/profile$/ },
    { from: "/forgot-password?next=%2Fprofile", to: /\/profile$/ },
  ];

  for (const { from, to } of cases) {
    const route = from.split("?")[0];
    test(`S4: a signed-in user opening ${route} is sent on to their next page`, async ({
      page,
      context,
    }) => {
      await context.addCookies([SESSION_COOKIE]);
      await installBackend(page, context, { authed: true });

      await page.goto(from);

      await expect(page).toHaveURL(to);
    });
  }
});

test.describe("session ends during a visit", () => {
  test("S5: a link to an already-prefetched page redirects to sign-in with the session-ended notice", async ({
    page,
    context,
  }) => {
    await context.addCookies([SESSION_COOKIE]);
    const backend = await installBackend(page, context, { authed: true });

    // On a production build the Profile link is prefetched once it is in the
    // viewport: wait for that RSC request so the click below can be served
    // from the router cache without reaching the middleware.
    const prefetched = page.waitForResponse(
      (r) => new URL(r.url()).pathname === "/profile" && r.url().includes("_rsc"),
      { timeout: 15_000 }
    ).catch(() => null); // `next dev` never prefetches; the test is still valid there
    await page.goto("/applications");
    const profileLink = page.getByTestId("app-sidebar").getByRole("link", { name: "Profile" });
    await expect(profileLink).toBeVisible();
    await prefetched;

    // The session dies server-side. The cookie is still in the jar (the
    // browser cannot know), so the click passes the gatekeeper and it is the
    // page's own API calls that find out: the first 401 is what the app must
    // turn into a redirect.
    backend.authed = false;

    await profileLink.click();

    await expect(page).toHaveURL(/\/login\?reason=expired&next=%2Fprofile/);
    const notice = page.getByTestId("session-ended");
    await expect(notice).toBeVisible();
    await expect(notice).toHaveText(BANNER);
    await expect(page.getByText(/authentication required/i)).toHaveCount(0);
  });

  test("S8/S9: when the session ends while a private list is open, a refocus signs out and clears the list", async ({
    page,
    context,
  }) => {
    await context.addCookies([SESSION_COOKIE]);
    const backend = await installBackend(page, context, { authed: true });

    await page.goto("/applications");
    await expect(page.getByText("Data Engineer")).toBeVisible();

    // The server forgets the session (expiry, password change, sign out
    // everywhere); /api/changes and /api/auth/me now answer 401. The user
    // returns to the tab.
    backend.authed = false;
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));

    await expect(page).toHaveURL(/\/login\?reason=expired&next=%2Fapplications/);
    await expect(page.getByTestId("session-ended")).toHaveText(BANNER);
    await expect(page.getByText("Data Engineer")).toHaveCount(0);
  });

  test("S10: a form submit that gets 401 sends the user to sign-in with a way back", async ({
    page,
    context,
  }) => {
    await context.addCookies([SESSION_COOKIE]);
    const backend = await installBackend(page, context, { authed: true });
    // The bring POST is the request that discovers the session is gone.
    await page.route("**/api/jobs/bring", async (route) => {
      backend.authed = false;
      await context.clearCookies();
      return route.fulfill(json({ detail: "authentication required" }, 401));
    });

    await page.goto("/bring");
    await page.getByLabel("Job title").fill("Senior Python Engineer");
    await page.getByLabel("Company").fill("Acme Ltd");
    await page.getByLabel("The ad, as written").fill("We are hiring a Senior Python Engineer.");
    await page.getByRole("button", { name: /bring this job/i }).click();

    await expect(page).toHaveURL(/\/login\?reason=expired&next=%2Fbring/);
    await expect(page.getByTestId("session-ended")).toHaveText(BANNER);
  });

  test("S11: opening / with a dead cookie does not leave the private dashboard on screen", async ({
    page,
    context,
  }) => {
    // The cookie is present (so / renders the Progress dashboard) but the
    // server rejects it: every /api/** call, /me included, answers 401.
    await context.addCookies([SESSION_COOKIE]);
    await installBackend(page, context, { authed: false });

    await page.goto("/");

    // Wait for the positive end state FIRST: the dashboard's own 401 sends the
    // user to sign in. Only then check nothing private is left. (Asserting
    // "no dashboard" straight after goto would pass before anything rendered.)
    await expect(page).toHaveURL(/\/login\?reason=expired$/);
    await expect(page.getByTestId("session-ended")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Progress dashboard" })).toHaveCount(0);
    await expect(page.getByText(/Could not load/)).toHaveCount(0);
  });
});

test.describe("logout", () => {
  test("S14: Back after logout does not show the private page", async ({ page, context }) => {
    await context.addCookies([SESSION_COOKIE]);
    await installBackend(page, context, { authed: true });

    await page.goto("/applications");
    await expect(page.getByRole("heading", { name: "Your applications" })).toBeVisible();

    await sidebarLogout(page).click();
    await expect(page).toHaveURL(/\/login/);

    await page.goBack();

    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByRole("heading", { name: "Your applications" })).toHaveCount(0);
  });

  test("S15: a logout the server never received says so, and works on the next try", async ({
    page,
    context,
  }) => {
    await context.addCookies([SESSION_COOKIE]);
    const backend = await installBackend(page, context, {
      authed: true,
      logout: "network-failure",
    });

    await page.goto("/applications");
    await expect(sidebarLogout(page)).toBeVisible();

    // The request never reaches the server, so the session is still alive.
    // The app must say so; a silent no-op (the old behaviour) is the bug.
    await sidebarLogout(page).click();
    await expect(
      page.getByText("We could not sign you out. Check your connection and try again.")
    ).toBeVisible();
    await expect(page).toHaveURL(/\/applications$/);

    backend.logout = "ok";
    await sidebarLogout(page).click();
    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByTestId("app-sidebar")).toHaveCount(0);
  });
});
