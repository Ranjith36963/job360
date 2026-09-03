import { test, expect } from "@playwright/test";

/**
 * Connect an agent — personal API tokens for the MCP server
 * (docs/plans/2026-09-03-mcp-server/spec.md).
 *
 * 1. Anonymous /settings/connect redirects to /login (middleware).
 * 2. Create a token → the plain token is shown ONCE with a ready-to-paste
 *    `claude mcp add` command that carries it as a Bearer header.
 * 3. The list shows only the prefix, never the token.
 * 4. Revoke → row gone, reveal gone.
 *
 * The backend is mocked with page.route — this proves the UI wiring. The
 * hash-only storage and the bearer path are backend/tests/test_api_tokens.py.
 */

const PLAIN_TOKEN = "j360_" + "a".repeat(43);

const TOKEN_ROW = {
  id: 7,
  name: "laptop Claude Code",
  prefix: "j360_aaaa",
  created_at: new Date().toISOString(),
  last_used_at: null as string | null,
};

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

test.describe("Connect an agent", () => {
  test("anonymous /settings/connect redirects to /login", async ({ page }) => {
    await page.goto("/settings/connect");
    await expect(page).toHaveURL(/\/login/);
  });

  test("create → token shown once with the connect command → revoke", async ({
    page,
    context,
  }) => {
    await context.addCookies([SESSION_COOKIE]);

    let tokens: (typeof TOKEN_ROW)[] = [];
    let createBody: Record<string, unknown> | null = null;
    let revokedId: string | null = null;

    await page.route("**/api/tokens", (route) => {
      if (route.request().method() === "POST") {
        createBody = route.request().postDataJSON() as Record<string, unknown>;
        tokens = [TOKEN_ROW];
        return route.fulfill(json({ ...TOKEN_ROW, token: PLAIN_TOKEN }, 201));
      }
      return route.fulfill(json({ tokens }));
    });
    await page.route("**/api/tokens/*", (route) => {
      if (route.request().method() === "DELETE") {
        revokedId = route.request().url().split("/").pop() ?? null;
        tokens = [];
        return route.fulfill({ status: 204, body: "" });
      }
      return route.continue();
    });

    await page.goto("/settings/connect");
    await expect(page).not.toHaveURL(/\/login/);
    await expect(page.getByRole("heading", { name: "Connect an agent" })).toBeVisible();
    await expect(page.getByTestId("tokens-empty")).toBeVisible({ timeout: 10_000 });

    // --- 1. Create -------------------------------------------------------
    await page.getByLabel("Name").fill(TOKEN_ROW.name);
    await page.getByRole("button", { name: "Create token" }).click();

    const reveal = page.getByTestId("token-reveal");
    await expect(reveal).toBeVisible({ timeout: 10_000 });
    expect(createBody).not.toBeNull();
    expect(createBody!.name).toBe(TOKEN_ROW.name);

    // --- 2. The token and the command, once ------------------------------
    await expect(page.getByTestId("token-value")).toHaveValue(PLAIN_TOKEN);
    const cmd = await page.getByTestId("connect-command").inputValue();
    expect(cmd).toContain("claude mcp add --transport http job360 ");
    expect(cmd).toContain("/api/mcp");
    expect(cmd).toContain(`--header "Authorization: Bearer ${PLAIN_TOKEN}"`);

    // --- 3. The list carries the prefix, not the token -------------------
    const rows = page.getByTestId("token-row");
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText(TOKEN_ROW.name);
    await expect(rows.first()).toContainText(TOKEN_ROW.prefix);
    await expect(rows.first()).not.toContainText(PLAIN_TOKEN);

    // Dismiss the reveal: it never comes back for this token.
    await page.getByTestId("token-reveal-done").click();
    await expect(reveal).toHaveCount(0);
    await expect(page.getByText(PLAIN_TOKEN)).toHaveCount(0);

    // --- 4. Revoke -------------------------------------------------------
    await page.getByRole("button", { name: `Revoke token ${TOKEN_ROW.name}` }).click();
    await expect(page.getByTestId("tokens-empty")).toBeVisible({ timeout: 10_000 });
    expect(revokedId).toBe(String(TOKEN_ROW.id));
  });
});
