import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: "html",
  use: {
    baseURL: "http://localhost:3000",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    // Run against a PRODUCTION build in CI, not `npm run dev`. Dev mode compiles
    // each route on its first hit; on the slower CI runner that first-navigation
    // compile exceeds Playwright's 30s test timeout and the login/dashboard/tailor
    // specs fail (even after 2 retries). A prebuilt server navigates instantly —
    // verified locally: 7 specs 1.9m (dev) -> 32s (start). Locally we still reuse a
    // running `npm run dev` for fast iteration.
    command: process.env.CI ? "npm run build && npm run start" : "npm run dev",
    url: "http://localhost:3000",
    reuseExistingServer: !process.env.CI,
    timeout: 240_000,
  },
});
