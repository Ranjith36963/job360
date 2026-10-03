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
