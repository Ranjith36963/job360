import { test, expect } from "@playwright/test";

/**
 * The People section on an application's detail page (slice 4, spec R4/S5,
 * docs/plans/2026-09-05-contacts-stats/spec.md).
 *
 * The backend is mocked with page.route — same hermetic style as
 * tests/e2e/bring-a-job.spec.ts — this proves the UI wiring, not the
 * database. The Postgres truth is backend/tests/test_slice4_contacts.py.
 */

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

// Mirrors backend ApplicationDetailOut (applications.py) field-for-field —
// ApplicationClient reads every one of these unguarded.
const BASE_DETAIL = {
  id: 42,
  job_id: 9001,
  status: "applied",
  created_at: "2026-09-01T09:00:00Z",
  updated_at: "2026-09-01T09:00:00Z",
  last_event_at: "2026-09-01T09:00:00Z",
  job: {
    job_title: "Senior Python Engineer",
    job_company: "Acme Ltd",
    job_location: "London, UK",
    job_url: "https://careers.example.com/jobs/123",
    job_source: "user_brought",
    job_description_snapshot: "Build our matching engine.",
    snapshot_at: "2026-09-01T09:00:00Z",
    catalog_present: true,
  },
  fit: null,
  artifacts: [],
  events: [],
  receipts: [],
  contacts: [] as Record<string, unknown>[],
};

const HTTPS_CONTACT = {
  id: 1,
  application_id: 42,
  name: "Jordan Lee",
  role: "Recruiter",
  email: "jordan@acme.example",
  linkedin_url: "https://linkedin.com/in/jordanlee",
  notes: "Met at the careers fair.",
  added_by: "web",
  created_at: "2026-09-01T10:00:00Z",
};

const BAD_LINK_CONTACT = {
  id: 2,
  application_id: 42,
  name: "Sam Rivera",
  role: "",
  email: "",
  linkedin_url: "javascript:alert(1)",
  notes: "",
  added_by: "agent:cli",
  created_at: "2026-09-01T11:00:00Z",
};

test.describe("Application detail — People section", () => {
  test("empty state points to the MCP tool, and adding a person appends it", async ({
    page,
    context,
  }) => {
    await context.addCookies([SESSION_COOKIE]);

    await page.route(`**/api/applications/${BASE_DETAIL.id}`, (route) => {
      if (route.request().method() === "GET") {
        return route.fulfill(json(BASE_DETAIL));
      }
      return route.continue();
    });

    let postedBody: Record<string, unknown> | null = null;
    const newContact = {
      id: 3,
      application_id: BASE_DETAIL.id,
      name: "Priya Patel",
      role: "Hiring Manager",
      email: "priya@acme.example",
      linkedin_url: "",
      notes: "",
      added_by: "web",
      created_at: "2026-09-02T08:00:00Z",
    };
    await page.route(`**/api/applications/${BASE_DETAIL.id}/contacts`, (route) => {
      postedBody = route.request().postDataJSON() as Record<string, unknown>;
      return route.fulfill(
        json({ contact: newContact, already_existed: false, event_id: 501 }, 201)
      );
    });

    await page.goto(`/applications/${BASE_DETAIL.id}`);
    await expect(page.getByRole("heading", { name: "People" })).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByText("No people yet.")).toBeVisible();
    await expect(page.getByText("add_contact")).toBeVisible();

    await page.getByLabel("Name").fill(newContact.name);
    await page.getByLabel("Role").fill(newContact.role);
    await page.getByLabel("Email").fill(newContact.email);
    await page.getByRole("button", { name: "Add person" }).click();

    expect(postedBody).not.toBeNull();
    expect(postedBody!.name).toBe(newContact.name);
    expect(postedBody!.role).toBe(newContact.role);

    await expect(page.getByTestId("contacts-list")).toContainText(newContact.name);
    await expect(page.getByTestId("contacts-list")).toContainText(newContact.role);
  });

  test("https:// LinkedIn renders as a link; a javascript: value renders as text (S5)", async ({
    page,
    context,
  }) => {
    await context.addCookies([SESSION_COOKIE]);

    const detail = { ...BASE_DETAIL, contacts: [HTTPS_CONTACT, BAD_LINK_CONTACT] };
    await page.route(`**/api/applications/${BASE_DETAIL.id}`, (route) =>
      route.fulfill(json(detail))
    );

    await page.goto(`/applications/${BASE_DETAIL.id}`);
    const list = page.getByTestId("contacts-list");
    await expect(list).toBeVisible({ timeout: 10_000 });

    await expect(list).toContainText(HTTPS_CONTACT.name);
    await expect(list).toContainText(BAD_LINK_CONTACT.name);

    // The https:// contact gets a real, clickable anchor…
    const goodLink = page.getByRole("link", { name: "LinkedIn" });
    await expect(goodLink).toHaveAttribute("href", HTTPS_CONTACT.linkedin_url);

    // …the javascript: contact's value is on the page as plain text, and
    // there is no anchor for it anywhere (S5: only https:// gets an href).
    await expect(list).toContainText(BAD_LINK_CONTACT.linkedin_url);
    await expect(page.locator(`a[href="${BAD_LINK_CONTACT.linkedin_url}"]`)).toHaveCount(0);
    // Scoped to the contacts list itself (not the whole page, which also has
    // nav/footer links) — exactly two links in there: the https:// contact's
    // LinkedIn anchor and their mailto: — never a link for the bad value.
    await expect(list.getByRole("link")).toHaveCount(2);
    await expect(list.locator(`a[href="mailto:${HTTPS_CONTACT.email}"]`)).toHaveCount(1);
  });

  test("a 422 from the server shows its detail text inline", async ({ page, context }) => {
    await context.addCookies([SESSION_COOKIE]);

    await page.route(`**/api/applications/${BASE_DETAIL.id}`, (route) =>
      route.fulfill(json(BASE_DETAIL))
    );
    await page.route(`**/api/applications/${BASE_DETAIL.id}/contacts`, (route) =>
      route.fulfill(
        json({ detail: "name exceeds CONTACT_NAME_MAX_CHARS (200)" }, 422)
      )
    );

    await page.goto(`/applications/${BASE_DETAIL.id}`);
    await expect(page.getByRole("heading", { name: "People" })).toBeVisible({
      timeout: 10_000,
    });

    await page.getByLabel("Name").fill("A very long name");
    await page.getByRole("button", { name: "Add person" }).click();

    await expect(
      page.getByText("name exceeds CONTACT_NAME_MAX_CHARS (200)")
    ).toBeVisible({ timeout: 10_000 });
  });
});
