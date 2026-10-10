import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { CountryCard } from "./CountryCard";

describe("CountryCard", () => {
  it("collapsed: name, first saved fact, amber '2 not saved yet', Open", () => {
    render(
      <CountryCard code="DE" saved={2} total={4} firstFact="Needs sponsorship: yes · saved by Claude, 3 Oct">
        <p>the rows</p>
      </CountryCard>,
    );
    expect(screen.getByText("Germany")).toBeInTheDocument();
    expect(screen.getByText("Needs sponsorship: yes · saved by Claude, 3 Oct")).toBeInTheDocument();
    expect(screen.getByTestId("country-missing-DE")).toHaveTextContent("2 not saved yet");
    expect(screen.getByTestId("country-missing-DE").className).toMatch(/text-warning/);
    expect(screen.queryByText("the rows")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Open Germany" }));
    expect(screen.getByText("the rows")).toBeInTheDocument();
  });

  it("complete: '4 of 4 saved' and no amber chip", () => {
    render(<CountryCard code="GB" saved={4} total={4} firstFact="ignored" defaultOpen><p>rows</p></CountryCard>);
    expect(screen.getByText("4 of 4 saved")).toBeInTheDocument();
    expect(screen.queryByTestId("country-missing-GB")).toBeNull();
    expect(screen.getByText("rows")).toBeInTheDocument();
  });

  it("nothing saved: '0 of 4 saved' and '4 not saved yet'", () => {
    render(<CountryCard code="FR" saved={0} total={4}><p>rows</p></CountryCard>);
    expect(screen.getByText("0 of 4 saved")).toBeInTheDocument();
    expect(screen.getByTestId("country-missing-FR")).toHaveTextContent("4 not saved yet");
  });
});
