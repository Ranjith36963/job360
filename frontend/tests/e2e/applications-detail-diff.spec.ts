import { test, expect, type Page } from "@playwright/test";

/**
 * Slice 8 (#515) — original vs tailored on the application page, read-only.
 * docs/plans/2026-09-11-cv-diff/spec.md R3, pinned here:
 *
 *   1. the version a receipt names carries an Applied badge (VISION decision
 *      26 — the receipt, not a button, says which version counts);
 *   2. Compare opens two panes with the removed line marked on the left and
 *      the added line marked on the right;
 *   3. there is no Keep button anywhere in the Artifacts section.
 *
 * Hermetic (frontend-only): fake the session cookie and mock every API call
 * with `page.route`, same as applications-detail-tailor.spec.ts.
 */

const SESSION_COOKIE = {
  name: "job360_session",
  value: "e2e-token",
  domain: "localhost",
  path: "/",
};

const APPLICATION_ID = 8282;
const JOB_ID = 556;
const CV_V1 = 901;
const CV_V2 = 902;

function artifact(id: number, version_no: number) {
  return {
    id,
    kind: "cv",
    version_no,
    made_by: "agent",
    model: "claude",
    profile_version: 7,
    label: "",
    chars: 80,
    created_at: "2026-09-10T00:00:00Z",
    text: null,
    truncated: false,
  };
}

function applicationDetail() {
  return {
    id: APPLICATION_ID,
    job_id: JOB_ID,
    status: "applied",
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-10T00:00:00Z",
    last_event_at: "2026-09-10T00:00:00Z",
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
    artifacts: [artifact(CV_V1, 1), artifact(CV_V2, 2)],
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
    receipts: [
      {
        id: 31,
        sent_at: "2026-09-10T00:00:00Z",
        channel: "company site",
        confirmation: "REF-123",
        cv_artifact_id: CV_V2,
        cover_letter_artifact_id: null,
        note: "",
      },
    ],
  };
}

function diffPayload() {
  return {
    kind: "cv",
    base: { source: "profile", artifact_id: null, version_no: null, label: "Original CV" },
    target: {
      artifact_id: CV_V2,
      version_no: 2,
      made_by: "agent",
      model: "claude",
      created_at: "2026-09-10T00:00:00Z",
      applied: true,
    },
    lines: [
      { op: "equal", text: "Jane Doe" },
      { op: "del", text: "Python, Postgres" },
      { op: "add", text: "Python, Postgres, Kubernetes" },
      { op: "equal", text: "Built fraud models at Acme." },
    ],
    added: 1,
    removed: 1,
    truncated: false,
  };
}

async function mockBackend(page: Page) {
  await page.route("**/api/auth/me**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ id: "e2e-user", email: "e2e@example.com" }),
    })
  );
  await page.route(`**/api/applications/${APPLICATION_ID}`, (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(applicationDetail()),
    });
  });
  await page.route(`**/api/applications/${APPLICATION_ID}/artifacts/${CV_V2}/diff**`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(diffPayload()),
    })
  );
}

test.describe("Application detail — original vs tailored (slice 8)", () => {
  test("marks the applied version, compares it, and offers no Keep", async ({ page, context }) => {
    await context.addCookies([SESSION_COOKIE]);
    await mockBackend(page);

    await page.goto(`/applications/${APPLICATION_ID}`);
    await expect(page.getByText("Platform Engineer")).toBeVisible({ timeout: 20_000 });

    // 1. the receipt names v2 → exactly one Applied badge, on v2's row
    const badges = page.getByTestId("artifact-applied-badge");
    await expect(badges).toHaveCount(1);
    const v2Row = page.getByTestId("artifact-version").filter({ has: badges });
    await expect(v2Row.getByTestId("artifact-version-label")).toHaveText("v2");

    // 2. Compare → removed on the left, added on the right, unchanged on both
    await v2Row.getByTestId("artifact-compare").click();
    const left = page.getByTestId("artifact-diff-left");
    const right = page.getByTestId("artifact-diff-right");
    await expect(left.locator('[data-diff="del"]')).toHaveText("Python, Postgres");
    await expect(right.locator('[data-diff="add"]')).toHaveText("Python, Postgres, Kubernetes");
    await expect(left.getByText("Jane Doe")).toBeVisible();
    await expect(right.getByText("Jane Doe")).toBeVisible();
    await expect(right.getByText("Applied version")).toBeVisible();

    // 3. no Keep — the receipt already said which version counts
    await expect(page.getByRole("button", { name: /keep/i })).toHaveCount(0);
  });
});
