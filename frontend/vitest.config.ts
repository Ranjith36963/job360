/// <reference types="vitest" />
import { defineConfig } from "vitest/config";
import path from "path";

export default defineConfig({
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    // Local runs on the owner's 16 GB Windows laptop starved for memory
    // (2026-10-02: 7 test files "Failed to start forks worker" mid-gate). Cap
    // local workers; CI keeps vitest's default. Override: VITEST_MAX_WORKERS=N.
    maxWorkers: process.env.CI ? undefined : (process.env.VITEST_MAX_WORKERS ?? 2),
    // 2026-10-04: even at 1 worker, "forks" kept missing vitest 5's fixed 60s
    // worker START_TIMEOUT on that laptop (a fresh process per file while
    // Windows swaps). Threads reuse the process: 70 files / 479 tests in 160s,
    // 0 start errors, vs ~400s with 1-2 start failures per run. CI keeps forks.
    pool: process.env.CI ? undefined : "threads",
    exclude: ["**/node_modules/**", "**/tests/e2e/**", "**/*.spec.ts"],
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov"],
      include: ["src/components/**", "src/lib/**"],
      exclude: ["src/test/**", "**/*.d.ts"],
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
