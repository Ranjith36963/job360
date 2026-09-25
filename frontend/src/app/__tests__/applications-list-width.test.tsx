/**
 * Regression guard (owner decision, 2026-09-25, round 3): the applications
 * list rendered as a narrow centred column — heading, "Bring a job" button,
 * filter chips and rows all pinned under a leftover `max-w-3xl mx-auto`
 * instead of PageContainer's `max-w-7xl`, even though `/applications`
 * itself had already been migrated. The actual bug was in `src/app/page.tsx`
 * (the signed-in HOME route, which reuses the same `ApplicationList` and is
 * what a screenshot of "the applications list" actually shows) — it still
 * hardcoded its own `mx-auto max-w-3xl` wrapper instead of `PageContainer`.
 *
 * `src/app/page.tsx` is an async Server Component (`next/headers` cookies)
 * and can't be rendered synchronously by React Testing Library (see
 * `landing-sources-count.test.tsx`), so it's checked by source text, the
 * same approach that file already uses for `layout.tsx`'s metadata.
 * `/applications` (a plain client component) is rendered for real.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import ApplicationsPage from "../applications/page";

vi.mock("@/lib/api", () => ({
  listApplications: vi.fn().mockResolvedValue({ applications: [], total: 0 }),
}));

/** Any max-width narrower than PageContainer's own `max-w-7xl`. A list
 * container carrying one of these instead of (or nested inside) `max-w-7xl`
 * is exactly the bug this test guards against. */
const NARROWER_MAX_W_RE = /\bmax-w-(3xs|2xs|xs|sm|md|lg|xl|2xl|3xl|4xl|5xl|6xl)\b/;

describe("Applications list — shares the navbar's max width (owner decision, 2026-09-25)", () => {
  it("/applications wraps its heading + list in PageContainer's max-w-7xl, not a narrower column", async () => {
    render(<ApplicationsPage />);
    const heading = await screen.findByText("Your applications");

    // Walk up from the heading to the outermost rendered element, collecting
    // every className on the way — the SAME check a browser's computed width
    // would answer, without needing a live layout engine.
    let node: HTMLElement | null = heading;
    const classes: string[] = [];
    while (node) {
      if (node.className) classes.push(node.className);
      node = node.parentElement;
    }
    const allClasses = classes.join(" ");

    expect(allClasses).toMatch(/\bmax-w-7xl\b/);
    expect(allClasses).not.toMatch(NARROWER_MAX_W_RE);
  });

  it("src/app/page.tsx (the signed-in home) uses PageContainer, not its own hardcoded max-width", () => {
    const source = readFileSync(join(process.cwd(), "src/app/page.tsx"), "utf-8");
    expect(source).toMatch(/PageContainer/);
    expect(source).not.toMatch(NARROWER_MAX_W_RE);
  });

  it("ApplicationList's own markup adds no competing max-width", () => {
    const source = readFileSync(
      join(process.cwd(), "src/components/applications/ApplicationList.tsx"),
      "utf-8"
    );
    expect(source).not.toMatch(NARROWER_MAX_W_RE);
  });
});
