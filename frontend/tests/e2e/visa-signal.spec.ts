import { test, expect, type Page } from "@playwright/test";

/**
 * Slice 7 (#514) — visa / sponsorship signal, country-agnostic.
 * docs/plans/2026-09-11-visa-signal/spec.md "Done when":
 *
 *   1. a red badge shows on a list row and the detail page for
 *      no_sponsorship; nothing for unknown;
 *   2. the dropdown PUTs the right body.
 *
 * Hermetic (frontend-only): fake the session cookie and mock every API call
 * with `page.route`, same pattern as applications-detail-diff.spec.ts.
 */

const SESSION_COOKIE = {
  name: "job360_session",
  value: "e2e-token",
  domain: "localhost",
  path: "/",
};

const APPLICATION_ID = 9191;
const JOB_ID = 333;

function applicationSummary(overrides: Record<string, unknown>) {
  return {
    id: APPLICATION_ID,
    job_id: JOB_ID,
    job_title: "Backend Engineer",
    job_company: "Acme",
    status: "considering",
    last_event_at: "2026-09-11T00:00:00Z",
    events: 1,
    artifacts: {},
    receipts: 0,
    visa_signal: "unknown",
    visa_country: "",
    needs_sponsorship: null,
    ...overrides,
  };
}

function applicationDetail(visa: {
  signal: string;
  detail: string;
  country: string;
  recorded_by: string;
  recorded_at: string;
  needs_sponsorship: boolean | null;
}) {
  return {
    id: APPLICATION_ID,
    job_id: JOB_ID,
    status: "considering",
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-11T00:00:00Z",
    last_event_at: "2026-09-11T00:00:00Z",
    job: {
      job_title: "Backend Engineer",
      job_company: "Acme",
      job_location: "Berlin",
      job_url: "https://acme.example/careers/3",
      job_source: "user_brought",
      job_description_snapshot: "Build the backend. Python, Postgres.",
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
    visa,
  };
}

async function mockAuth(page: Page) {
  await page.route("**/api/auth/me**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ id: "e2e-user", email: "e2e@example.com" }),
    })
  );
}

test.describe("Visa signal — applications home", () => {
  test("exactly one no_sponsorship badge shows; the unknown row shows none", async ({
    page,
    context,
  }) => {
    await context.addCookies([SESSION_COOKIE]);
    await mockAuth(page);

    await page.route("**/api/whats-new**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          now: "2026-09-11T00:00:00Z",
          since: "2026-09-04T00:00:00Z",
          next_since: "2026-09-11T00:00:00Z",
          next_after_id: 1,
          truncated: false,
          applications: [],
          events: [],
        }),
      })
    );

    await page.route("**/api/applications?**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          applications: [
            applicationSummary({
              id: APPLICATION_ID,
              visa_signal: "no_sponsorship",
              needs_sponsorship: true,
            }),
            applicationSummary({
              id: APPLICATION_ID + 1,
              job_title: "Frontend Engineer",
              visa_signal: "unknown",
              needs_sponsorship: null,
            }),
          ],
          total: 2,
        }),
      })
    );

    await page.goto("/applications");
    await expect(page.getByText(/backend engineer/i).first()).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(/frontend engineer/i).first()).toBeVisible();

    const badges = page.getByTestId("visa-badge");
    await expect(badges).toHaveCount(1);
    await expect(badges.first()).toHaveAttribute("data-visa", "no_sponsorship");
  });
});

test.describe("Visa signal — application detail", () => {
  test("shows the red badge and detail quote, then PUTs the chosen signal", async ({
    page,
    context,
  }) => {
    await context.addCookies([SESSION_COOKIE]);
    await mockAuth(page);

    let putBody: unknown = null;

    await page.route(`**/api/applications/${APPLICATION_ID}`, (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          applicationDetail({
            signal: "no_sponsorship",
            detail: "We cannot offer visa sponsorship.",
            country: "DE",
            recorded_by: "agent",
            recorded_at: "2026-09-11T00:00:00Z",
            needs_sponsorship: true,
          })
        ),
      });
    });

    await page.route(`**/api/applications/${APPLICATION_ID}/visa`, (route) => {
      if (route.request().method() !== "PUT") return route.fallback();
      putBody = route.request().postDataJSON();
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          application_id: APPLICATION_ID,
          visa: {
            signal: "sponsors",
            detail: "We cannot offer visa sponsorship.",
            country: "DE",
            recorded_by: "web",
            recorded_at: "2026-09-11T00:05:00Z",
            needs_sponsorship: null,
          },
        }),
      });
    });

    await page.goto(`/applications/${APPLICATION_ID}`);
    await expect(page.getByText("Backend Engineer")).toBeVisible({ timeout: 20_000 });

    const badge = page.getByTestId("visa-badge");
    await expect(badge).toHaveAttribute("data-visa", "no_sponsorship");
    await expect(page.getByTestId("visa-detail").first()).toBeVisible();
    await expect(page.getByText("We cannot offer visa sponsorship.").first()).toBeVisible();

    // The select control hydrates from the current visa values.
    await expect(page.getByTestId("visa-select")).toHaveValue("no_sponsorship");
    await expect(page.getByTestId("visa-country")).toHaveValue("DE");

    await page.getByTestId("visa-select").selectOption("sponsors");
    await page.getByTestId("visa-save").click();

    await expect.poll(() => putBody).toEqual({
      visa_signal: "sponsors",
      visa_country: "DE",
      visa_detail: "We cannot offer visa sponsorship.",
    });
  });
});
