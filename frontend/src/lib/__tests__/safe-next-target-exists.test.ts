/**
 * Regression guard: the post-login fallback must be a page that exists.
 *
 * Slice 5 (#483) deleted `src/app/dashboard/page.tsx` while `safeNext()` still
 * fell back to "/dashboard" — so every password login and magic-link login
 * with no `?next` would have landed on a 404 in production. The unit tests
 * stayed green because they pinned the string, not the page. This test pins
 * the page: whatever `safeNext()` falls back to must have a `page.tsx` under
 * `src/app/`.
 */
import { existsSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { safeNext } from "../safe-next";

describe("safeNext fallback", () => {
  it("points at a page that exists under src/app", () => {
    const fallback = safeNext(null);
    expect(fallback.startsWith("/")).toBe(true);
    const pageFile = join(__dirname, "..", "..", "app", ...fallback.slice(1).split("/"), "page.tsx");
    expect(existsSync(pageFile), `${fallback} has no page.tsx at ${pageFile}`).toBe(true);
  });
});
