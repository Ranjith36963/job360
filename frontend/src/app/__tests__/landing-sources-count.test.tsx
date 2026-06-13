/**
 * Regression guard: the landing page must show "46" for the source count,
 * not the stale "50" that was present before the fix.
 *
 * The page is a server component with no client hooks, so we can render it
 * directly with @testing-library/react. We mock next/link to avoid the
 * App-Router runtime dependency.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import Home from "../page";

// Mock next/link — not available in jsdom
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: React.AnchorHTMLAttributes<HTMLAnchorElement> & { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

describe("Landing page — source count copy", () => {
  it('stats bar shows "46" for Sources, not "50"', () => {
    render(<Home />);
    // The STATS bar renders value="46" above the label "Sources"
    const statValue = screen.getByText("46");
    expect(statValue).toBeInTheDocument();
  });

  it('feature card title is "46 Job Sources", not "50 Job Sources"', () => {
    render(<Home />);
    expect(screen.getByText("46 Job Sources")).toBeInTheDocument();
    expect(screen.queryByText("50 Job Sources")).not.toBeInTheDocument();
  });

  it('hero heading says "46 Sources.", not "50 Sources."', () => {
    render(<Home />);
    expect(screen.getByText("46 Sources.")).toBeInTheDocument();
    expect(screen.queryByText("50 Sources.")).not.toBeInTheDocument();
  });

  it("hero paragraph mentions 46 job sources, not 50", () => {
    render(<Home />);
    // The subtitle paragraph contains "46 job sources."
    expect(screen.getByText(/46 job sources\./i)).toBeInTheDocument();
    expect(screen.queryByText(/50 job sources\./i)).not.toBeInTheDocument();
  });
});
