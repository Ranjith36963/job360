/**
 * The one Job360 logo: ring-and-dot symbol plus the wordmark. The accessible
 * name "job360" lives on the link that wraps it; the SVG is decorative.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import Link from "next/link";
import { Logo } from "@/components/brand/Logo";

describe("Logo", () => {
  it("renders the wordmark text job360 with 360 in its own span", () => {
    render(<Logo />);

    const word = screen.getByTestId("logo-wordmark");
    expect(word).toHaveTextContent("job360");
    expect(word.querySelector("span")).toHaveTextContent("360");
  });

  it("gives a link wrapping it the accessible name job360", () => {
    render(
      <Link href="/" aria-label="job360">
        <Logo />
      </Link>
    );

    expect(screen.getByRole("link", { name: "job360" })).toBeInTheDocument();
  });

  it("keeps the SVG decorative", () => {
    const { container } = render(<Logo />);

    expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });

  it("symbolOnly hides the wordmark text but keeps the symbol", () => {
    const { container } = render(<Logo symbolOnly />);

    expect(screen.queryByTestId("logo-wordmark")).toBeNull();
    expect(container).not.toHaveTextContent("job360");
    expect(container.querySelector("svg")).not.toBeNull();
  });

  it("gives two logos on one page different mask ids", () => {
    const { container } = render(
      <>
        <Logo />
        <Logo />
      </>
    );

    const ids = Array.from(container.querySelectorAll("mask")).map((m) => m.id);
    expect(ids).toHaveLength(2);
    expect(new Set(ids).size).toBe(2);
    const refs = Array.from(container.querySelectorAll("circle[mask]")).map((c) =>
      c.getAttribute("mask")
    );
    expect(refs).toEqual(ids.map((id) => `url(#${id})`));
  });

  it("always draws the symbol in #00FF00", () => {
    const { container } = render(<Logo />);

    expect(container.querySelector("circle[stroke]")).toHaveAttribute("stroke", "#00FF00");
    expect(container.querySelector("svg > circle:not([mask])")).toHaveAttribute("fill", "#00FF00");
  });
});
