/**
 * Regression guard: the landing page never advertises a job-source count.
 *
 * WHY THIS TEST WAS REWRITTEN (spec 2026-09-04-application-spine, R14)
 *
 * This file used to assert the landing page's FIVE mentions of `SOURCE_COUNT`
 * were all internally consistent. R14 removed sourcing from the pitch
 * entirely — "Job360 never sources or ranks jobs" (VISION rule 4) — so the
 * previous assertions (checking a number that must no longer appear) would
 * now be asserting the wrong thing. This test now asserts the OPPOSITE: no
 * "<N> source(s)" copy survives anywhere on the landing page.
 *
 * `src/app/page.tsx` is now an async Server Component (it reads the session
 * cookie via `next/headers` to decide Landing vs. the applications home) and
 * can no longer be rendered synchronously by React Testing Library, so this
 * test renders `Landing` (the extracted marketing page) directly.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import Landing from "../Landing";
import { Footer } from "@/components/layout/Footer";

// Mock next/link — not available in jsdom
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: React.AnchorHTMLAttributes<HTMLAnchorElement> & { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

const SOURCE_COUNT_RE = /\d{1,3}\s+(?:job\s+)?sources?\b/gi;

describe("Landing page — no source-count copy (R14)", () => {
  it("never mentions a job-source count", () => {
    const { container } = render(<Landing />);
    const text = container.textContent ?? "";
    const mentions = [...text.matchAll(SOURCE_COUNT_RE)];
    expect(mentions).toHaveLength(0);
  });

  it("still renders the hero headline", () => {
    const { getByText } = render(<Landing />);
    expect(getByText("Your CV.")).toBeInTheDocument();
  });
});

// C2 (application-spine review) — the Footer sits on EVERY page (mounted
// once in layout.tsx), so a source-count claim there reaches a signed-in
// user Landing.tsx never renders for. Same for the exported <head> metadata
// (title/description/OpenGraph/Twitter) — a crawler or a share-card reads
// that text, never the rendered DOM.
describe("Footer — no source-count copy (R14, C2)", () => {
  it("never mentions a job-source count", () => {
    const { container } = render(<Footer />);
    const text = container.textContent ?? "";
    expect([...text.matchAll(SOURCE_COUNT_RE)]).toHaveLength(0);
  });
});

describe("Root layout metadata — no source-count copy (R14, C2)", () => {
  it("never mentions a job-source count in title/description/OpenGraph/Twitter", () => {
    // Read the source text rather than `import { metadata } from "../layout"`:
    // layout.tsx also pulls in `next/font/google`, which needs the Next.js
    // compiler and cannot be imported directly under plain Vite/vitest.
    // vitest runs with cwd = the frontend package root (vitest.config.ts).
    const layoutPath = join(process.cwd(), "src/app/layout.tsx");
    const source = readFileSync(layoutPath, "utf-8");
    const metadataBlock = source.slice(source.indexOf("export const metadata"));
    expect([...metadataBlock.matchAll(SOURCE_COUNT_RE)]).toHaveLength(0);
    expect(metadataBlock).not.toMatch(/\d+D scoring/i);
  });
});
