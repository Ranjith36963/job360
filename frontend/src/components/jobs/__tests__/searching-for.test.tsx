/**
 * "Searching for: X, Y, Z [change]" — the line that makes the guess visible.
 *
 * The product claim being pinned: a user who sees wrong results must be able to
 * reach "wrong setting" instead of "this product does not understand me". That
 * needs two things on screen — the derived queries, and a one-click route to
 * the field that changes them. Both are asserted here.
 *
 * The other half of the contract is SILENCE. This line is a nicety; if it has
 * nothing true to say (no profile yet, empty list, failed fetch → undefined) it
 * must render nothing at all. An empty "Searching for:" or an error box in its
 * place would make the product look broken over an optional hint.
 */

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { SearchingFor } from "../SearchingFor";

describe("SearchingFor", () => {
  it("shows the titles we actually send to the job boards", () => {
    render(<SearchingFor titles={["AI Engineer", "ML Engineer", "Data Scientist"]} />);

    const line = screen.getByTestId("searching-for");
    expect(line).toHaveTextContent("Searching for:");
    expect(line).toHaveTextContent("AI Engineer, ML Engineer, Data Scientist");
  });

  it("links [change] straight to the Target Job Titles field", () => {
    render(<SearchingFor titles={["Data Scientist"]} />);

    const change = screen.getByRole("link", { name: /change/i });
    // The hash is load-bearing: /profile alone drops the user at the top of a
    // long page and the fix is one field near the bottom.
    expect(change).toHaveAttribute("href", "/profile#target-job-titles");
  });

  it("renders NOTHING when the list is empty", () => {
    const { container } = render(<SearchingFor titles={[]} />);
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText(/searching for/i)).toBeNull();
  });

  it("renders NOTHING when the prop is missing (fetch failed / no profile)", () => {
    const { container } = render(<SearchingFor />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders NOTHING when the list is all blanks", () => {
    // A list of whitespace is still nothing to say. Without the trim+filter
    // this would render "Searching for:  ,   [change]".
    const { container } = render(<SearchingFor titles={["", "   "]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("drops blank entries but keeps the real ones", () => {
    render(<SearchingFor titles={["  Backend Engineer  ", "", "Platform Engineer"]} />);

    expect(screen.getByTestId("searching-for")).toHaveTextContent(
      "Backend Engineer, Platform Engineer"
    );
  });
});
