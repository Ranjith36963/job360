import { test, expect, type Page } from "@playwright/test";

/**
 * The application spine — the owner's done-when, walked hermetically
 * (docs/plans/2026-09-04-application-spine/spec.md §Frozen tests, item 38).
 *
 * "The owner brings a job, saves two CV versions, records 'applied' with the
 * second... and the web home show exactly that, every version still readable."
 *
 * Hermetic (frontend-only), same pattern as the other e2e specs: fake the
 * session cookie (middleware only checks presence) and mock every API call
 * with `page.route`. The Dashboard nav link is gone for good (R12/R14; the
 * route itself was deleted outright in slice 5, delete-sourcing-era).
 */

const SESSION_COOKIE = {
  name: "job360_session",
  value: "e2e-token",
  domain: "localhost",
  path: "/",
};

const APPLICATION_ID = 4242;
const JOB_ID = 777;

const CV_TEXT: Record<number, string> = {
  1: "CV VERSION ONE — the first draft",
  2: "CV VERSION TWO — the one actually sent",
};

function applicationSummary(status: string) {
  return {
    id: APPLICATION_ID,
    job_id: JOB_ID,
    job_title: "Data Engineer",
    job_company: "Northwind",
    job_url: "https://northwind.example/careers/7",
    status,
    last_event_at: "2026-09-04T00:00:00Z",
    events: status === "considering" ? 1 : 2,
    artifacts: { cv: 2 },
    receipts: status === "considering" ? 0 : 1,
    // 2026-09-24 — the list card's "Next:" line reads this, same shape
    // `get_application`'s next_step already carries.
    next_step:
      status === "considering"
        ? { code: "apply", label: "CV ready — apply, then mark it applied" }
        : { code: "wait", label: "Applied — waiting to hear back" },
  };
}

function applicationDetail(status: string) {
  const events = [
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
    },
  ];
  if (status === "applied") {
    events.push({
      id: 2,
      event_type: "applied",
      detail: "",
      payload: {},
      occurred_at: "2026-09-02T00:00:00Z",
      recorded_at: "2026-09-02T00:00:00Z",
      recorded_by: "web",
      corrects_event_id: null,
      superseded: false,
    });
  }
  return {
    id: APPLICATION_ID,
    job_id: JOB_ID,
    status,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-02T00:00:00Z",
    last_event_at: "2026-09-02T00:00:00Z",
    job: {
      job_title: "Data Engineer",
      job_company: "Northwind",
      job_location: "Remote",
      job_url: "https://northwind.example/careers/7",
      job_source: "user_brought",
      job_description_snapshot: "Build the pipelines. Python, dbt, Snowflake.",
      snapshot_at: "2026-09-01T00:00:00Z",
      catalog_present: true,
    },
    fit: null,
    artifacts: [
      {
        id: 1, kind: "cv", version_no: 1, made_by: "web", model: null,
        profile_version: 1, label: "", chars: CV_TEXT[1].length, created_at: "2026-09-01T00:05:00Z",
      },
      {
        id: 2, kind: "cv", version_no: 2, made_by: "web", model: null,
        profile_version: 1, label: "", chars: CV_TEXT[2].length, created_at: "2026-09-02T00:00:00Z",
      },
    ],
    events,
    receipts:
      status === "applied"
        ? [
            {
              id: 1, sent_at: "2026-09-02T00:00:00Z", channel: "company site",
              confirmation: "", cv_artifact_id: 2, cover_letter_artifact_id: null, note: "",
            },
          ]
        : [],
    next_step:
      status === "considering"
        ? { code: "apply", label: "CV ready — apply, then mark it applied" }
        : { code: "wait", label: "Applied — waiting to hear back" },
  };
}

/** A mutable-status backend double: everything the home + record pages need,
 * with `status` flipping in place the moment the receipt route is hit — so a
 * SINGLE page session sees "considering" then "applied" without a reload. */
async function mockBackend(page: Page) {
  let status: "considering" | "applied" = "considering";

  await page.route("**/api/auth/me**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ id: "e2e-user", email: "e2e@example.com" }),
    })
  );

  await page.route("**/api/applications?**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ applications: [applicationSummary(status)], total: 1 }),
    })
  );

  await page.route(`**/api/applications/${APPLICATION_ID}`, (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(applicationDetail(status)),
    });
  });

  await page.route(`**/api/applications/${APPLICATION_ID}/receipt`, (route) => {
    status = "applied";
    route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        receipt_id: 1, sent_at: "2026-09-02T00:00:00Z", cv_artifact_id: 2, cv_version_no: 2,
        cover_letter_artifact_id: null, channel: "company site", confirmation: "",
        url: `/applications/${APPLICATION_ID}`, event_id: 2,
      }),
    });
  });

  for (const [idStr, text] of Object.entries(CV_TEXT)) {
    const id = Number(idStr);
    await page.route(`**/api/applications/${APPLICATION_ID}/artifacts/${id}`, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id, kind: "cv", version_no: id, text, made_by: "web", model: null,
          profile_version: 1, label: "", chars: text.length, created_at: "2026-09-01T00:00:00Z",
        }),
      })
    );
  }
}

test.describe("Applications home — the spine, end to end (hermetic)", () => {
  test("brought -> applied shows on the home; the record lists both CV versions, both readable", async ({
    page,
    context,
  }) => {
    await context.addCookies([SESSION_COOKIE]);
    await mockBackend(page);
    // Accept any native confirm() the "Mark Applied" button on the
    // application page raises — a no-op when it uses something else.
    page.on("dialog", (dialog) => void dialog.accept());

    await page.goto("/");

    // The brought application is on the signed-in home, status "considering".
    await expect(page.getByText(/data engineer/i).first()).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(/considering/i).first()).toBeVisible();

    // R12/R14 — the search flag is off by default, so the old Dashboard link
    // must not exist on a page a signed-in user can navigate.
    await expect(page.getByRole("link", { name: /^dashboard$/i })).toHaveCount(0);

    // Owner decision (2026-09-24): "Mark Applied" writes a permanent receipt,
    // so it no longer lives on the scannable list — only on the application
    // page, behind a confirm. Open the record and record "applied" there.
    await page.goto(`/applications/${APPLICATION_ID}`);
    await expect(page.getByText(/data engineer/i).first()).toBeVisible({ timeout: 20_000 });

    await page
      .getByRole("button", { name: /mark.*applied|i applied|record application|applied/i })
      .first()
      .click();
    await expect(page.getByText(/^applied$/i).first()).toBeVisible({ timeout: 20_000 });

    // The list reflects the same change — reload the home page.
    await page.goto("/");
    await expect(page.getByText(/^applied$/i).first()).toBeVisible({ timeout: 20_000 });
    // ...and the list row no longer offers a Mark Applied button at all.
    await expect(
      page.getByRole("button", { name: /mark applied/i })
    ).toHaveCount(0);

    // Open the application record: two CV versions listed, both open with
    // their own text — "every version still readable" (done-when).
    await page.goto(`/applications/${APPLICATION_ID}`);
    await expect(page.getByText(/data engineer/i).first()).toBeVisible({ timeout: 20_000 });

    const v1 = page.getByText(/v(ersion)?\.?\s*1\b/i).first();
    const v2 = page.getByText(/v(ersion)?\.?\s*2\b/i).first();
    await expect(v1).toBeVisible();
    await expect(v2).toBeVisible();

    await v1.click();
    await expect(page.getByText(CV_TEXT[1])).toBeVisible({ timeout: 10_000 });
    await v2.click();
    await expect(page.getByText(CV_TEXT[2])).toBeVisible({ timeout: 10_000 });

    // Six-question reorder (2026-09-20) — Fit answers "how do I compare",
    // the first question a seeker has, so it now leads; History (the old
    // Timeline) is folded at the bottom. The one receipt in the mock renders
    // in the Sent section.
    const timelineSection = page.getByTestId("section-timeline");
    const fitSection = page.getByTestId("section-fit");
    await expect(timelineSection).toBeVisible();
    await expect(fitSection).toBeVisible();
    const timelineBox = await timelineSection.boundingBox();
    const fitBox = await fitSection.boundingBox();
    expect(timelineBox).not.toBeNull();
    expect(fitBox).not.toBeNull();
    expect(fitBox!.y).toBeLessThan(timelineBox!.y);

    await expect(page.getByTestId("receipt-row")).toHaveCount(1);
  });
});
