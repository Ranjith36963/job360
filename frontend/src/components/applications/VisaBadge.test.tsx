import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { VisaBadge } from "./VisaBadge";

// docs/plans/2026-09-11-visa-signal/spec.md — "The badge (web, pure)" table.
describe("VisaBadge", () => {
  it('renders nothing for signal "unknown"', () => {
    const { container } = render(<VisaBadge signal="unknown" needsSponsorship={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders a green "Sponsors visas" pill for signal "sponsors"', () => {
    render(<VisaBadge signal="sponsors" needsSponsorship={null} />);
    const badge = screen.getByTestId("visa-badge");
    expect(badge).toHaveTextContent("Sponsors visas");
    expect(badge).toHaveAttribute("data-visa", "sponsors");
    expect(badge.className).toContain("bg-emerald-500/15");
  });

  it('renders a red "No sponsorship" pill when needsSponsorship is true', () => {
    render(<VisaBadge signal="no_sponsorship" needsSponsorship={true} />);
    const badge = screen.getByTestId("visa-badge");
    expect(badge).toHaveTextContent("No sponsorship");
    expect(badge).toHaveAttribute("data-visa", "no_sponsorship");
    expect(badge.className).toContain("bg-red-500/15");
  });

  it('renders a red "No sponsorship" pill when needsSponsorship is null (unknown comparison)', () => {
    render(<VisaBadge signal="no_sponsorship" needsSponsorship={null} />);
    const badge = screen.getByTestId("visa-badge");
    expect(badge).toHaveTextContent("No sponsorship");
    expect(badge).toHaveAttribute("data-visa", "no_sponsorship");
    expect(badge.className).toContain("bg-red-500/15");
  });

  it('renders a muted "not needed for you" pill when needsSponsorship is false', () => {
    render(<VisaBadge signal="no_sponsorship" needsSponsorship={false} />);
    const badge = screen.getByTestId("visa-badge");
    expect(badge).toHaveTextContent("No sponsorship · not needed for you");
    expect(badge).toHaveAttribute("data-visa", "no_sponsorship_covered");
    expect(badge.className).toContain("bg-muted");
  });

  it("renders the detail as a quote line under the pill when given", () => {
    render(
      <VisaBadge
        signal="no_sponsorship"
        needsSponsorship={true}
        detail="We cannot offer visa sponsorship."
      />
    );
    expect(screen.getByTestId("visa-detail")).toHaveTextContent(
      "We cannot offer visa sponsorship."
    );
  });

  it("renders no detail line when detail is empty", () => {
    render(<VisaBadge signal="sponsors" needsSponsorship={null} detail="" />);
    expect(screen.queryByTestId("visa-detail")).toBeNull();
  });
});
