import { test, expect, type APIRequestContext } from "@playwright/test";

/**
 * TRUE end-to-end: no page.route, no cookie faking, no E2E_TEST_MODE. A real
 * Chromium drives the real frontend, which proxies /api/* to the real backend
 * on a real Postgres (CI job `fullstack-e2e`; see playwright.fullstack.config.ts).
 *
 * One journey, one fresh user, steps in order:
 *   a. register + sign in through the real UI
 *   b. bring a job on /bring (pasted ad, not the URL fetch: that needs the network)
 *   c. open the Application it created; assert the ad text the backend stored (value, not heading)
 *   d. an agent saves a CV (MCP save_artifact), the human edits it in the tailor view,
 *      reload, the edit persisted
 *   e. MCP list_applications / get_application with the personal token: the application is there
 *
 * Step d needs a CV to exist and Job360 has no LLM of its own (decision 28), so
 * the "agent" is the MCP save_artifact tool, i.e. the product's own door. That is
 * why the token is minted before d and not at e.
 */

const BACKEND = process.env.FULLSTACK_BACKEND_URL ?? "http://127.0.0.1:8000";

const RUN = `${Date.now()}${Math.floor(Math.random() * 1000)}`;
const EMAIL = `fullstack-${RUN}@example.com`;
const PASSWORD = "Fullstack-e2e-pass-1";
const TITLE = `Fullstack Engineer ${RUN}`;
const COMPANY = `Acme Fullstack ${RUN}`;
const AD_TEXT =
  `We are hiring a Fullstack Engineer (${RUN}) to build our matching engine.\n\n` +
  "You will own FastAPI services and Postgres, and ship a Next.js frontend.";
const AGENT_CV = `Agent-written CV ${RUN}\n\nPython, FastAPI, Postgres.`;
const HUMAN_CV = `Human-edited CV ${RUN}\n\nPython, FastAPI, Postgres. Edited by hand in the tailor view.`;

type RpcMessage = {
  id?: number;
  error?: unknown;
  result?: {
    isError?: boolean;
    structuredContent?: Record<string, unknown>;
    content?: Array<{ text: string }>;
  };
};

/** One JSON-RPC tools/call against the backend's streamable-HTTP MCP mount. */
async function callTool(
  request: APIRequestContext,
  token: string,
  name: string,
  args: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const res = await request.post(`${BACKEND}/api/mcp`, {
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      // The transport answers as SSE or JSON depending on the build; accept both.
      Accept: "application/json, text/event-stream",
    },
    data: { jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: args } },
  });
  expect(res.status(), `MCP ${name} HTTP status`).toBe(200);
  const raw = await res.text();
  // SSE may carry notifications (tools/list_changed) before the reply: take the one with our id.
  const messages: RpcMessage[] = raw.trimStart().startsWith("{")
    ? [JSON.parse(raw)]
    : raw
        .split("\n")
        .filter((l) => l.startsWith("data:"))
        .map((l) => JSON.parse(l.slice(5).trim()));
  const rpc: RpcMessage = messages.find((m) => m.id === 1) ?? {};
  expect(rpc.result, `MCP ${name} reply: ${raw.slice(0, 500)}`).toBeDefined();
  expect(rpc.error, `MCP ${name} JSON-RPC error`).toBeUndefined();
  expect(rpc.result?.isError, `MCP ${name} tool error: ${raw.slice(0, 400)}`).toBeFalsy();
  if (rpc.result?.structuredContent) return rpc.result.structuredContent;
  return JSON.parse(rpc.result?.content?.[0]?.text ?? "{}");
}

test("register -> bring -> application -> tailor edit -> MCP, on the real stack", async ({
  page,
  context,
}) => {
  // ── a. Register through the real UI, then sign in ──────────────────────────
  // Registering sends no session and the verification mail goes to a mailer that
  // is not configured here; the CI backend runs with REQUIRE_EMAIL_VERIFICATION=false.
  await page.goto("/register");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/login\?registered=1/);
  await expect(page.getByText("Almost there")).toBeVisible();
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/profile/);
  const cookies = await context.cookies();
  expect(cookies.some((c) => c.name === "job360_session" && c.value.length > 10)).toBe(true);

  // ── b. Bring a job by pasting the ad ───────────────────────────────────────
  await page.goto("/bring");
  await page.getByLabel("Job title").fill(TITLE);
  await page.getByLabel("Company", { exact: true }).fill(COMPANY);
  await page.getByLabel("The ad, as written").fill(AD_TEXT);
  await page.getByRole("button", { name: "Bring this job" }).click();
  await page.waitForURL(/\/applications\/\d+$/);
  const applicationId = Number(page.url().match(/\/applications\/(\d+)$/)![1]);

  // ── c. The Application page, and the ad text the backend stored ────────────
  await expect(page.getByRole("heading", { name: TITLE })).toBeVisible();
  await expect(page.getByText(COMPANY).first()).toBeVisible();
  // The page does not render the ad text itself (reported, not fixed), so the
  // value is read from the same endpoint the page loads, through the frontend's
  // /api proxy with the browser's own session cookie.
  const detail = await context.request.get(`/api/applications/${applicationId}`);
  expect(detail.status()).toBe(200);
  const body = await detail.json();
  expect(body.job.job_description_snapshot).toBe(AD_TEXT);
  expect(body.job.job_title).toBe(TITLE);
  expect(body.job.job_company).toBe(COMPANY);

  // ── Mint the personal token the way the product does (POST /api/tokens) ───
  const minted = await context.request.post("/api/tokens", { data: { name: `fullstack-e2e-${RUN}` } });
  expect(minted.status()).toBe(201);
  const token: string = (await minted.json()).token;
  expect(token).toMatch(/^j360_/);

  // ── d. The agent saves a CV, the human edits it in the tailor view ─────────
  const saved = await callTool(context.request, token, "save_artifact", {
    kind: "cv",
    text: AGENT_CV,
    application_id: applicationId,
  });
  expect(saved.version_no).toBe(1);

  await page.reload();
  await page.getByRole("button", { name: /edit & download saved documents/i }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("textbox")).toHaveValue(AGENT_CV);
  await dialog.getByRole("textbox").fill(HUMAN_CV);
  await dialog.getByRole("button", { name: "Save as new version" }).click();
  await expect(dialog.getByText(/v2 · saved by/)).toBeVisible();

  await page.reload();
  await page.getByRole("button", { name: /edit & download saved documents/i }).click();
  await expect(page.getByRole("dialog").getByRole("textbox")).toHaveValue(HUMAN_CV);

  // ── e. MCP: the application is in the agent's list, with the edited CV ─────
  const list = await callTool(context.request, token, "list_applications", {});
  const rows = (list.applications ?? list.items ?? []) as Array<Record<string, unknown>>;
  const mine = rows.find((r) => (r.id ?? r.application_id) === applicationId);
  expect(mine, `application ${applicationId} in list_applications: ${JSON.stringify(list).slice(0, 400)}`).toBeTruthy();
  expect(JSON.stringify(mine)).toContain(COMPANY);

  const one = await callTool(context.request, token, "get_application", {
    application_id: applicationId,
    with_artifact_text: true,
  });
  const artifacts = (one.artifacts ?? []) as Array<Record<string, unknown>>;
  expect(artifacts.map((a) => a.text)).toContain(HUMAN_CV);
});
