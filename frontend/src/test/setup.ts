import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import { resetSharedReads } from "@/lib/shared-read";

afterEach(() => {
  cleanup();
  // The shared readers (needs-you-count, assistant settings) keep a settled answer per key for a few
  // seconds, so a test must not inherit the previous test's answer for the same key.
  resetSharedReads();
});
