import { defineConfig, devices } from "@playwright/test";

// TRUE end-to-end config: real Chromium -> real Next.js frontend -> real FastAPI
// backend -> real Postgres. Unlike frontend/playwright.config.ts (specs in tests/e2e/,
// API mocked with page.route, E2E_TEST_MODE auth bypass) there is NO webServer
// here and NO mocking: whoever runs this (the `fullstack-e2e` job in
// .github/workflows/ci-offline.yml, or a developer) starts Postgres, the
// backend and the frontend first. FULLSTACK_BASE_URL is the frontend origin,
// FULLSTACK_BACKEND_URL the backend (the MCP step calls it directly: /api/mcp
// is streamable HTTP, so it does not go through the Next.js rewrite proxy).
const BASE_URL = process.env.FULLSTACK_BASE_URL ?? "http://localhost:3000";

export default defineConfig({
  testDir: ".",
  // Paths resolve against THIS file's folder; keep the report and results where
  // the CI job's upload step (and a developer) expects them: frontend/.
  outputDir: "../../test-results",
  // One user walks one journey; the steps share state, so no parallelism.
  fullyParallel: false,
  workers: 1,
  // No retries: a retry would re-register and hide a flaky product path.
  retries: 0,
  forbidOnly: !!process.env.CI,
  timeout: 120_000,
  expect: { timeout: 20_000 },
  reporter: [["html", { outputFolder: "../../playwright-report", open: "never" }]],
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        // Optional: a developer box whose Chromium is not the build this
        // Playwright expects can point at one. CI leaves it unset.
        launchOptions: process.env.FULLSTACK_CHROMIUM_PATH
          ? { executablePath: process.env.FULLSTACK_CHROMIUM_PATH }
          : {},
      },
    },
  ],
});
