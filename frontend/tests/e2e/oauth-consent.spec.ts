import { test, expect } from "@playwright/test";

/**
 * OAuth 2.1 consent screen + connected apps
 * (docs/plans/2026-09-03-oauth-mcp/spec.md R4, R8, R9, S9).
 *
 * 1. Anonymous /oauth/consent/[rid] bounces to /login?next=/oauth/consent/[rid]
 *    (middleware — PROTECTED_PATHS gains "/oauth").
 * 2. Signed in: the consent page shows the client name, the FULL redirect
 *    URI, "Signed in as <email>", the unverified-name line and the scope
 *    description. Allow / Deny POST {approve} and follow `redirect_to`.
 * 3. A 404 from the GET (unknown/consumed/expired request) shows the expired
 *    copy and no Allow button.
 * 4. /settings/connect lists connected apps (oauth_grants) and Revoke calls
 *    DELETE and removes the row.
 * 5. /auth/magic honours ?next — including falling back to /applications for
 *    an external next.
 *
 * The backend is mocked with page.route — this proves the UI wiring, same
 * style as tests/e2e/connect-agent.spec.ts. Server-side OAuth logic is
 * backend/tests/test_oauth_server.py.
 */

const RID = "abc";

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

const CONSENT = {
  client_name: "Claude",
  redirect_uri: "https://claude.ai/api/mcp/auth_callback",
  scope: "job360",
  scope_description:
    "read your profile, bring jobs, tailor documents and record applications",
  user_email: "user@example.com",
  expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
};

test.describe("OAuth consent — anonymous", () => {
  test("redirects to /login with next=/oauth/consent/abc", async ({ page }) => {
    await page.goto(`/oauth/consent/${RID}`);
    await expect(page).toHaveURL(/\/login\?next=%2Foauth%2Fconsent%2Fabc/);
  });
});

test.describe("OAuth consent — signed in", () => {
  test("shows client, full redirect URI, email, scope; Allow approves and navigates", async ({
    page,
    context,
  }) => {
    await context.addCookies([SESSION_COOKIE]);

    let decisionBody: Record<string, unknown> | null = null;

    await page.route(`**/api/oauth/authorize/${RID}`, (route) => {
      if (route.request().method() === "GET") {
        return route.fulfill(json(CONSENT));
      }
      return route.continue();
    });
    await page.route(`**/api/oauth/authorize/${RID}/decision`, (route) => {
      decisionBody = route.request().postDataJSON() as Record<string, unknown>;
      return route.fulfill(json({ redirect_to: "/dashboard?code=x" }));
    });

    await page.goto(`/oauth/consent/${RID}`);
    await expect(page).not.toHaveURL(/\/login/);

    await expect(page.getByTestId("consent-client-name")).toContainText(
      CONSENT.client_name
    );
    await expect(page.getByTestId("consent-redirect-uri")).toContainText(
      CONSENT.redirect_uri
    );
    await expect(page.getByTestId("consent-user-email")).toContainText(
      CONSENT.user_email
    );
    await expect(page.getByText(/its name is not verified/i)).toBeVisible();
    await expect(page.getByText(CONSENT.scope_description)).toBeVisible();

    await page.getByTestId("consent-allow").click();

    await expect(page).toHaveURL(/\/dashboard\?code=x/, { timeout: 10_000 });
    expect(decisionBody).toEqual({ approve: true });
  });

  test("Deny posts approve:false and follows redirect_to", async ({
    page,
    context,
  }) => {
    await context.addCookies([SESSION_COOKIE]);

    let decisionBody: Record<string, unknown> | null = null;

    await page.route(`**/api/oauth/authorize/${RID}`, (route) => {
      if (route.request().method() === "GET") {
        return route.fulfill(json(CONSENT));
      }
      return route.continue();
    });
    await page.route(`**/api/oauth/authorize/${RID}/decision`, (route) => {
      decisionBody = route.request().postDataJSON() as Record<string, unknown>;
      return route.fulfill(json({ redirect_to: "/dashboard?denied=1" }));
    });

    await page.goto(`/oauth/consent/${RID}`);
    await page.getByTestId("consent-deny").click();

    await expect(page).toHaveURL(/\/dashboard\?denied=1/, { timeout: 10_000 });
    expect(decisionBody).toEqual({ approve: false });
  });

  test("404 from the GET shows expired copy, no Allow button", async ({
    page,
    context,
  }) => {
    await context.addCookies([SESSION_COOKIE]);

    await page.route(`**/api/oauth/authorize/${RID}`, (route) => {
      if (route.request().method() === "GET") {
        return route.fulfill(json({ detail: "not found" }, 404));
      }
      return route.continue();
    });

    await page.goto(`/oauth/consent/${RID}`);

    await expect(page.getByTestId("consent-expired")).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByTestId("consent-allow")).toHaveCount(0);
  });
});

test.describe("Connected apps (oauth_grants) on /settings/connect", () => {
  const GRANT_ROW = {
    id: 3,
    client_name: "Claude",
    redirect_uri: "https://claude.ai/api/mcp/auth_callback",
    scope: "job360",
    created_at: new Date().toISOString(),
    last_used_at: null as string | null,
  };

  test("lists connected apps and Revoke calls DELETE and removes the row", async ({
    page,
    context,
  }) => {
    await context.addCookies([SESSION_COOKIE]);

    let grants: (typeof GRANT_ROW)[] = [GRANT_ROW];
    let revokedId: string | null = null;

    await page.route("**/api/tokens", (route) => route.fulfill(json({ tokens: [] })));
    await page.route("**/api/oauth/grants", (route) => {
      if (route.request().method() === "GET") {
        return route.fulfill(json({ grants }));
      }
      return route.continue();
    });
    await page.route("**/api/oauth/grants/*", (route) => {
      if (route.request().method() === "DELETE") {
        revokedId = route.request().url().split("/").pop() ?? null;
        grants = [];
        return route.fulfill({ status: 204, body: "" });
      }
      return route.continue();
    });

    await page.goto("/settings/connect");
    await expect(page).not.toHaveURL(/\/login/);

    const rows = page.getByTestId("grant-row");
    await expect(rows).toHaveCount(1, { timeout: 10_000 });
    await expect(rows.first()).toContainText(GRANT_ROW.client_name);
    await expect(rows.first()).toContainText("claude.ai");

    await page.getByTestId(`grant-revoke-${GRANT_ROW.id}`).click();
    await page.getByTestId(`grant-revoke-confirm-${GRANT_ROW.id}`).click();

    await expect(page.getByTestId("grants-empty")).toBeVisible({ timeout: 10_000 });
    expect(revokedId).toBe(String(GRANT_ROW.id));
  });
});

test.describe("/auth/magic honours ?next", () => {
  test("lands on the OAuth consent page named in next", async ({ page, context }) => {
    await context.addCookies([SESSION_COOKIE]);

    await page.route("**/api/auth/magic-link/consume", (route) =>
      route.fulfill(json({ id: "u1", email: "user@example.com" }))
    );
    await page.route(`**/api/oauth/authorize/${RID}`, (route) => {
      if (route.request().method() === "GET") {
        return route.fulfill(json(CONSENT));
      }
      return route.continue();
    });

    await page.goto(`/auth/magic?token=t&next=%2Foauth%2Fconsent%2F${RID}`);
    await page.getByRole("button", { name: /sign in to job360/i }).click();

    await expect(page).toHaveURL(new RegExp(`/oauth/consent/${RID}`), {
      timeout: 10_000,
    });
  });

  test("falls back to /applications when next is an external URL", async ({
    page,
    context,
  }) => {
    await context.addCookies([SESSION_COOKIE]);

    await page.route("**/api/auth/magic-link/consume", (route) =>
      route.fulfill(json({ id: "u1", email: "user@example.com" }))
    );

    await page.goto(`/auth/magic?token=t&next=${encodeURIComponent("https://evil.com")}`);
    await page.getByRole("button", { name: /sign in to job360/i }).click();

    // safeNext (src/lib/safe-next.ts) falls back to /applications — /dashboard
    // left with the sourcing era.
    await expect(page).toHaveURL(/\/applications/, { timeout: 10_000 });
  });
});
