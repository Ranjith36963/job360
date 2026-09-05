import { test, expect, type Page } from "@playwright/test";

/**
 * URL fetch on the web — frozen tests for /bring's link box
 * (docs/plans/2026-09-04-url-fetch/spec.md, items 38-40).
 *
 * These selectors/behaviour are pinned on `frontend/src/app/bring/page.tsx`'s
 * link-fetch box, alongside the existing paste form.
 *
 * THE ASSUMED CONTRACT (frozen by this spec — build the page to satisfy it)
 * -----------------------------------------------------------------------
 * Spec R12 says "a link input + Fetch button above the existing fields" but
 * does not name selectors, so this spec pins them (house style elsewhere in
 * /bring is `getByLabel` on an accessible label — see bring-a-job.spec.ts):
 *
 *   - A text/url input labelled "Fetch a job from a link" (getByLabel).
 *   - A button named "Fetch" (getByRole("button", { name: /^fetch$/i })).
 *   - Pressing Enter in that input triggers the fetch and does NOT submit
 *     the surrounding <form> (R12: "it never submits the form").
 *   - On outcome "ok": the four existing fields (Job title / Company /
 *     Location / The ad, as written) are filled from the response, the
 *     existing "Link to the ad" field (#bring-url, label "Link to the ad")
 *     holds `final_url`, and focus moves to the title field. Each field
 *     named in `found` carries a `data-testid="bring-filled-<field>"`
 *     marker element (R12: "filled from the link — check it").
 *   - On any other outcome: a single element `data-testid="bring-fetch-
 *     outcome"` shows the outcome's message (non-empty, and DISTINCT per
 *     outcome — item 40 forces the server to send an EMPTY message so this
 *     can only pass if the frontend owns its own copy map, exactly as
 *     plan.md's `url-fetch-messages.ts` describes, rather than merely
 *     echoing the server string). The "Link to the ad" field keeps the
 *     ORIGINAL typed link, and the paste box (#bring-description, label
 *     "The ad, as written") is left empty, enabled, and focused.
 *   - The bring form itself is NEVER auto-submitted by a fetch, success or
 *     failure — no `POST /api/jobs/bring` is ever observed in this spec.
 *
 * Hermetic (frontend-only): fake the session cookie (middleware only checks
 * presence) and mock every API call with `page.route`, same pattern as
 * feed-visibility.spec.ts / bring-a-job.spec.ts.
 */

const SESSION_COOKIE = {
  name: "job360_session",
  value: "e2e-token",
  domain: "localhost",
  path: "/",
};

const json = (body: unknown, status = 200) => ({
  status,
  contentType: "application/json",
  body: JSON.stringify(body),
});

// The eight closed outcomes (spec R3) — kept here, not imported, because this
// spec must fail loudly if a new one is added to the backend without a
// matching frontend message, per item 40's own point.
const OUTCOMES = [
  "ok",
  "ssrf_denied",
  "invalid_url",
  "unreachable",
  "blocked",
  "timeout",
  "too_large",
  "unsupported_content",
] as const;

function fetchUrlResponse(overrides: Record<string, unknown>) {
  return {
    outcome: "ok",
    message: "",
    final_url: "",
    redirects: 0,
    title: "",
    company: "",
    location: "",
    description: "",
    found: [] as string[],
    source_hint: "",
    bytes_read: 0,
    elapsed_ms: 0,
    ...overrides,
  };
}

async function mockCommonLayout(page: Page) {
  await page.route("**/api/status**", (route) =>
    route.fulfill(json({ jobs_total: 0, last_run: null, sources: [] }))
  );
  await page.route("**/api/pipeline**", (route) => route.fulfill(json({ applications: [] })));
}

test.describe("Bring a job — fetch from a link", () => {
  test("fill a link + Fetch (Enter) fills the form and never auto-submits it", async ({
    page,
    context,
  }) => {
    await context.addCookies([SESSION_COOKIE]);
    await mockCommonLayout(page);

    const url = "https://boards.greenhouse.io/acme/jobs/12345";
    const response = fetchUrlResponse({
      outcome: "ok",
      message: "Filled from the link — check it.",
      final_url: url,
      redirects: 0,
      title: "Senior Backend Engineer",
      company: "Acme Corp",
      location: "London, England, GB",
      description: "We are looking for a Senior Backend Engineer to join our platform team.",
      found: ["title", "company", "location", "description"],
      source_hint: "json_ld",
      bytes_read: 4096,
      elapsed_ms: 120,
    });

    let fetchUrlCalls = 0;
    await page.route("**/api/jobs/fetch-url", (route) => {
      fetchUrlCalls += 1;
      expect(route.request().postDataJSON()).toEqual({ url });
      return route.fulfill(json(response));
    });

    let bringSubmitted = false;
    await page.route("**/api/jobs/bring", (route) => {
      bringSubmitted = true;
      return route.continue();
    });

    await page.goto("/bring");
    await expect(page.getByRole("heading", { name: "Bring a job" })).toBeVisible();

    const linkInput = page.getByLabel(/fetch a job from a link/i);
    await linkInput.fill(url);
    // R12: Enter fetches; it must NEVER submit the surrounding form.
    await linkInput.press("Enter");

    await expect
      .poll(() => fetchUrlCalls, { timeout: 10_000 })
      .toBeGreaterThan(0);

    // The four fields are filled from the response.
    await expect(page.getByLabel("Job title")).toHaveValue(response.title);
    await expect(page.getByLabel("Company")).toHaveValue(response.company);
    await expect(page.getByLabel(/^Location/)).toHaveValue(response.location);
    await expect(page.getByLabel("The ad, as written")).toHaveValue(response.description);

    // apply_url holds final_url.
    await expect(page.getByLabel(/Link to the ad/)).toHaveValue(url);

    // Every field the response said it filled carries a "filled" marker.
    for (const field of response.found) {
      await expect(page.getByTestId(`bring-filled-${field}`)).toBeVisible();
    }

    // Focus moves to the title field.
    await expect(page.getByLabel("Job title")).toBeFocused();

    // The bring form itself was never submitted by the fetch.
    expect(bringSubmitted).toBe(false);
  });

  test("a blocked outcome shows the paste-fallback message, keeps the link, empties the paste box", async ({
    page,
    context,
  }) => {
    await context.addCookies([SESSION_COOKIE]);
    await mockCommonLayout(page);

    const url = "https://www.linkedin.com/jobs/view/123456";
    const message = "LinkedIn blocked this fetch — paste the ad text instead.";
    const response = fetchUrlResponse({ outcome: "blocked", message, final_url: "" });

    await page.route("**/api/jobs/fetch-url", (route) => route.fulfill(json(response)));
    let bringSubmitted = false;
    await page.route("**/api/jobs/bring", (route) => {
      bringSubmitted = true;
      return route.continue();
    });

    await page.goto("/bring");
    const linkInput = page.getByLabel(/fetch a job from a link/i);
    await linkInput.fill(url);
    await page.getByRole("button", { name: /^fetch$/i }).click();

    // The outcome's message is shown INLINE (not a toast).
    await expect(page.getByTestId("bring-fetch-outcome")).toHaveText(message, { timeout: 10_000 });

    // The link is KEPT — the user should not have to retype it to paste instead.
    await expect(page.getByLabel(/Link to the ad/)).toHaveValue(url);

    // The paste box is empty, enabled, and focused — never disabled, never
    // silently filled with something a failed fetch half-produced.
    const pasteBox = page.getByLabel("The ad, as written");
    await expect(pasteBox).toHaveValue("");
    await expect(pasteBox).toBeEnabled();
    await expect(pasteBox).toBeFocused();

    expect(bringSubmitted).toBe(false);
  });

  test("every outcome renders its own distinct, non-empty message", async ({ page, context }) => {
    await context.addCookies([SESSION_COOKIE]);
    await mockCommonLayout(page);

    await page.goto("/bring");
    const linkInput = page.getByLabel(/fetch a job from a link/i);
    const fetchBtn = page.getByRole("button", { name: /^fetch$/i });

    const seen: Record<string, string> = {};

    for (const outcome of OUTCOMES) {
      // The server message is deliberately EMPTY: if the UI merely echoed
      // `response.message`, every outcome would render nothing here. A
      // non-empty, per-outcome result proves the frontend owns its own copy
      // map (plan.md: url-fetch-messages.ts) rather than trusting the wire.
      const response = fetchUrlResponse({
        outcome,
        message: "",
        final_url: outcome === "ok" ? "https://example.test/job" : "",
        found: outcome === "ok" ? ["title"] : [],
        title: outcome === "ok" ? "Some Job" : "",
      });

      await page.unroute("**/api/jobs/fetch-url").catch(() => undefined);
      await page.route("**/api/jobs/fetch-url", (route) => route.fulfill(json(response)));

      await linkInput.fill(`https://example.test/${outcome}`);
      await fetchBtn.click();

      const messageEl = page.getByTestId("bring-fetch-outcome");
      await expect(messageEl).not.toHaveText("", { timeout: 10_000 });
      seen[outcome] = (await messageEl.textContent()) ?? "";

      await linkInput.fill("");
    }

    const texts = Object.values(seen);
    expect(new Set(texts).size).toBe(OUTCOMES.length);
    for (const outcome of OUTCOMES) {
      expect(seen[outcome].length).toBeGreaterThan(0);
    }
  });

  // B5 (adversarial review, 2026-09-04) — an "ok" outcome used to
  // unconditionally overwrite every field, so a response that filled title/
  // description but left company empty (a real ladder result: the heuristic
  // rung has no company signal) blanked out whatever the user had already
  // typed there. Per-field `if (res.x) setX(res.x)` fixes it — this pins
  // that a user-typed value survives an "ok" fetch that came back empty for
  // that one field.
  test("a typed company survives an ok fetch whose response leaves company empty", async ({
    page,
    context,
  }) => {
    await context.addCookies([SESSION_COOKIE]);
    await mockCommonLayout(page);

    const url = "https://careers.northwind.example.test/roles/backend-engineer";
    const response = fetchUrlResponse({
      outcome: "ok",
      final_url: url,
      title: "Backend Engineer",
      company: "", // the heuristic rung found no company signal
      description: "We need a backend engineer for our warehouse systems.",
      found: ["title", "description"],
      source_hint: "heuristic",
    });

    await page.route("**/api/jobs/fetch-url", (route) => route.fulfill(json(response)));

    await page.goto("/bring");
    const companyInput = page.getByLabel("Company");
    await companyInput.fill("Acme Corp (typed by hand)");

    const linkInput = page.getByLabel(/fetch a job from a link/i);
    await linkInput.fill(url);
    await page.getByRole("button", { name: /^fetch$/i }).click();

    await expect(page.getByLabel("Job title")).toHaveValue(response.title, { timeout: 10_000 });
    // The company field the user typed BEFORE fetching must survive — the
    // response's empty company must never clobber it.
    await expect(companyInput).toHaveValue("Acme Corp (typed by hand)");
  });
});
