import { test, expect, type Page, type BrowserContext } from "@playwright/test";

/**
 * Landing-CTA → Profile journeys.
 *
 * The homepage CTAs ("Get Started" / "Upload Your CV") link to /profile, a
 * route protected by middleware.ts. This spec locks in the contract that a
 * logged-OUT visitor is sent through sign-in/registration first and only
 * reaches /profile once authenticated, while a logged-IN visitor goes straight
 * there. Three journeys:
 *   1. New visitor  → register → /profile
 *   2. Returning    → sign in  → /profile
 *   3. Already in   → straight to /profile
 *
 * Hermetic: middleware only checks for the PRESENCE of the job360_session
 * cookie (validity is enforced server-side), and AuthProvider never redirects
 * on a failed /api/auth/me — so we fake the cookie and mock the auth endpoints
 * rather than depending on a live backend (CI runs the frontend only). The real
 * cookie-minting + credential checks are covered by the backend test suite.
 */

const SESSION_COOKIE = {
  name: "job360_session",
  value: "e2e-token",
  domain: "localhost",
  path: "/",
};

const USER = { id: "e2e-user", email: "e2e@example.com" };

// Make an authenticated page render cleanly without a backend: a valid /me and
// a "no profile yet" /profile (404 is the real new-user response; the page
// renders its empty state and does NOT redirect).
async function mockAuthedApis(page: Page) {
  await page.route("**/api/auth/me**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(USER),
    })
  );
  await page.route("**/api/profile**", (route) =>
    route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: "no profile" }),
    })
  );
}

async function signedIn(context: BrowserContext) {
  await context.addCookies([SESSION_COOKIE]);
}

test.describe("Landing CTA → profile journeys", () => {
  // ── Logged out: the CTAs gate behind sign-in, remembering /profile ────────

  test("logged-out 'Get Started' redirects to sign-in with next=/profile", async ({
    page,
  }) => {
    await page.goto("/");
    await page.getByRole("link", { name: /get started/i }).click();
    // Generous timeout: Next dev cold-compiles /login on first hit.
    await expect(page).toHaveURL(/\/login\?next=%2Fprofile/, { timeout: 40_000 });
  });

  test("logged-out 'Upload Your CV' redirects to sign-in with next=/profile", async ({
    page,
  }) => {
    await page.goto("/");
    await page.getByRole("link", { name: /upload your cv/i }).click();
    await expect(page).toHaveURL(/\/login\?next=%2Fprofile/, { timeout: 40_000 });
  });

  // ── Journey 3: already signed in → straight to /profile ───────────────────

  test("signed-in 'Get Started' goes straight to /profile (no sign-in detour)", async ({
    page,
    context,
  }) => {
    await signedIn(context);
    await mockAuthedApis(page);

    await page.goto("/");
    await page.getByRole("link", { name: /get started/i }).click();

    // Generous timeout: /profile may cold-compile on the first navigation.
    await expect(page).toHaveURL(/\/profile/, { timeout: 40_000 });
    await expect(page).not.toHaveURL(/\/login/);
  });

  // ── Journey 2: returning user → sign in → /profile ────────────────────────

  test("returning user signing in on the gated /login lands on /profile", async ({
    page,
    context,
  }) => {
    // The server would set this cookie on a successful login; we pre-seed it so
    // middleware passes when the form pushes to /profile. The assertion under
    // test is the navigation contract (the form honours ?next), not cookie
    // minting.
    await signedIn(context);
    await mockAuthedApis(page);
    await page.route("**/api/auth/login**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(USER),
      })
    );

    await page.goto("/login?next=%2Fprofile");
    // The form defaults to magic-link; switch to the password form FIRST — each
    // form has its OWN email input, so fill email+password AFTER switching or the
    // password form submits with an empty email and validation blocks the redirect.
    await page.getByRole("button", { name: /use password instead/i }).click();
    await page.getByLabel(/email/i).fill("returning@example.com");
    await page.getByLabel(/password/i).fill("Password123");
    await page.getByRole("button", { name: /^sign in$/i }).click();

    await expect(page).toHaveURL(/\/profile/, { timeout: 40_000 });
  });

  // ── Journey 1: new visitor → create account → /profile ────────────────────

  test("new visitor creating an account is sent to sign in", async ({
    page,
    context,
  }) => {
    await signedIn(context);
    await mockAuthedApis(page);
    await page.route("**/api/auth/register**", (route) =>
      route.fulfill({
        status: 201,
        contentType: "application/json",
        // M2 — register now returns a generic body (no user), and no cookie.
        body: JSON.stringify({ status: "ok", message: "Please sign in to continue." }),
      })
    );

    await page.goto("/register");
    await page.getByLabel(/email/i).fill("newvisitor@example.com");
    await page.getByLabel(/password/i).fill("Password123");
    await page.getByRole("button", { name: /create account/i }).click();

    // M2 — register no longer auto-logs-in (that would leak whether an email
    // exists), so a fresh sign-up is sent to /login to sign in. `next` is
    // preserved so they still land on /profile after signing in.
    await expect(page).toHaveURL(/\/login\?registered=1/, { timeout: 40_000 });
  });
});
