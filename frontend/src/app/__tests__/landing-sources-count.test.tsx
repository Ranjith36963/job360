/**
 * Regression guard: the landing page must show "47" for the source count,
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
  it('stats bar shows "47" for Sources, not "50"', () => {
    render(<Home />);
    // The STATS bar renders value="47" above the label "Sources"
    const statValue = screen.getByText("47");
    expect(statValue).toBeInTheDocument();
  });

  it('feature card title is "47 Job Sources", not "50 Job Sources"', () => {
    render(<Home />);
    expect(screen.getByText("47 Job Sources")).toBeInTheDocument();
    expect(screen.queryByText("50 Job Sources")).not.toBeInTheDocument();
  });

  it('hero heading says "47 Sources.", not "50 Sources."', () => {
    render(<Home />);
    expect(screen.getByText("47 Sources.")).toBeInTheDocument();
    expect(screen.queryByText("50 Sources.")).not.toBeInTheDocument();
  });

  it("hero paragraph mentions 47 job sources, not 50", () => {
    render(<Home />);
    // The subtitle paragraph contains "47 job sources."
    expect(screen.getByText(/47 job sources\./i)).toBeInTheDocument();
    expect(screen.queryByText(/50 job sources\./i)).not.toBeInTheDocument();
  });
});
