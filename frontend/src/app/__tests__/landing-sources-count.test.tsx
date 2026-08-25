/**
 * Regression guard: every source count on the landing page agrees with the ONE
 * constant, and no stale literal survives anywhere in the copy.
 *
 * WHY THIS TEST WAS REWRITTEN
 *
 * The previous version said in its docstring that it guarded "the live
 * SOURCE_REGISTRY size", then hardcoded `47` and asserted only that the page
 * did not say `46`. Six sources were pruned on 2026-08-17, the registry became
 * 41, and this test went on passing — its passing is exactly what kept the
 * landing page telling every visitor a number that was wrong by six.
 *
 * A test that names a literal freezes that literal. So this one names none.
 * It asserts the rendered numbers are CONSISTENT with `SOURCE_COUNT`, and that
 * no other 2-digit count is lurking next to the word "source".
 *
 * The tie back to the backend registry is not this test's job and cannot be —
 * jsdom cannot read Python. `scripts/doc_sync_check.py` (guard
 * `landing-source-count`) compares `SOURCE_COUNT` against `SOURCE_REGISTRY`
 * and is mutation-tested, so it is known to be able to fail.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import Home from "../page";
import { SOURCE_COUNT } from "@/lib/catalog";

// Mock next/link — not available in jsdom
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: React.AnchorHTMLAttributes<HTMLAnchorElement> & { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

describe("Landing page — source count copy", () => {
  it("stats bar shows the constant for Sources", () => {
    render(<Home />);
    expect(screen.getByText(String(SOURCE_COUNT))).toBeInTheDocument();
  });

  it("feature card title uses the constant", () => {
    render(<Home />);
    expect(screen.getByText(`${SOURCE_COUNT} Job Sources`)).toBeInTheDocument();
  });

  it("hero heading uses the constant", () => {
    render(<Home />);
    expect(screen.getByText(`${SOURCE_COUNT} Sources.`)).toBeInTheDocument();
  });

  it("hero paragraph uses the constant", () => {
    render(<Home />);
    expect(
      screen.getByText(new RegExp(`${SOURCE_COUNT} job sources\\.`, "i")),
    ).toBeInTheDocument();
  });

  it("no OTHER two-digit count appears beside the word 'source'", () => {
    // The real failure was five copies of one number, only some of which any
    // single assertion looked at. This sweeps the whole rendered document, so
    // a sixth mention added later cannot quietly disagree with the other five.
    const { container } = render(<Home />);
    const text = container.textContent ?? "";
    const mentions = [...text.matchAll(/(\d{1,3})\s+(?:job\s+)?sources?\b/gi)];
    expect(mentions.length).toBeGreaterThan(0);
    for (const m of mentions) {
      expect(Number(m[1])).toBe(SOURCE_COUNT);
    }
  });
});
