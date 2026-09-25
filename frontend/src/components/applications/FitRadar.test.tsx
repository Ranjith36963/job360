import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { FitRadar } from "./FitRadar";

const AXES = [
  { name: "LLM depth", role: 100, you: 0 },
  { name: "Production ML", role: 50, you: 50 },
  { name: "Leadership", role: 0, you: 100 },
  { name: "London base", role: 100, you: 100 },
];

function pointsOf(testId: string): number[][] {
  const attr = screen.getByTestId(testId).getAttribute("points") ?? "";
  return attr.split(" ").map((pair) => pair.split(",").map(Number));
}

describe("FitRadar", () => {
  it("draws one label per axis and a shape per side, with the agent's numbers on them", () => {
    render(<FitRadar axes={AXES} size={200} />);

    const labels = screen.getAllByTestId("fit-radar-axis");
    expect(labels.map((el) => el.textContent)).toEqual(AXES.map((a) => a.name));

    // size 200 → centre (100,100), radius 64. Axis 0 points straight up.
    const role = pointsOf("fit-radar-role");
    const you = pointsOf("fit-radar-you");
    expect(role).toHaveLength(4);
    expect(you).toHaveLength(4);
    expect(role[0]).toEqual([100, 36]); // role 100 on axis 0 → on the outer ring
    expect(you[0]).toEqual([100, 100]); // you 0 on axis 0 → the centre
    expect(you[2]).toEqual([100, 164]); // you 100 on axis 2 (straight down)

    const legend = document.querySelector("figcaption");
    expect(legend?.textContent).toContain("The role asks");
    expect(legend?.textContent).toContain("You bring");
  });

  it("shows a visible table with one row per axis and the plain numbers", () => {
    render(<FitRadar axes={AXES} size={200} />);

    const table = screen.getByTestId("fit-radar-table");
    expect(table).toBeVisible();

    const rows = screen.getAllByTestId("fit-radar-row");
    expect(rows).toHaveLength(AXES.length);
    expect(rows.map((row) => Array.from(row.querySelectorAll("td")).map((td) => td.textContent))).toEqual([
      ["LLM depth", "100", "0"],
      ["Production ML", "50", "50"],
      ["Leadership", "0", "100"],
      ["London base", "100", "100"],
    ]);

    // Column headers name what the numbers mean.
    expect(screen.getByRole("columnheader", { name: "Line" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "The role asks" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "You bring" })).toBeInTheDocument();
  });

  it("draws the value text at the same font size as the axis label (owner decision 2026-09-25)", () => {
    render(<FitRadar axes={AXES} size={200} />);

    const label = screen.getAllByTestId("fit-radar-axis")[0];
    const value = screen.getAllByTestId("fit-radar-axis-value")[0];
    expect(value.getAttribute("font-size")).toBe(label.getAttribute("font-size"));
  });

  it("draws a small 'asks N · you N' value line under each axis label", () => {
    render(<FitRadar axes={AXES} size={200} />);

    const values = screen.getAllByTestId("fit-radar-axis-value");
    expect(values.map((el) => el.textContent)).toEqual([
      "asks 100 · you 0",
      "asks 50 · you 50",
      "asks 0 · you 100",
      "asks 100 · you 100",
    ]);
  });

  it("draws nothing with fewer than three axes — no picture, no placeholder", () => {
    const { container } = render(<FitRadar axes={AXES.slice(0, 2)} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("clamps a stray value into 0..100 rather than drawing outside the rings", () => {
    render(<FitRadar axes={[{ name: "a", role: 250, you: -20 }, AXES[1], AXES[2]]} size={200} />);
    expect(pointsOf("fit-radar-role")[0]).toEqual([100, 36]);
    expect(pointsOf("fit-radar-you")[0]).toEqual([100, 100]);
  });

  it("wraps a long axis label into two lines and widens the viewBox so it isn't clipped", () => {
    const longName = "CS fundamentals (distributed, HPC)";
    const { container } = render(
      <FitRadar axes={[{ name: longName, role: 75, you: 40 }, AXES[1], AXES[2]]} size={200} />
    );

    const longLabel = screen
      .getAllByTestId("fit-radar-axis")
      .find((el) => el.textContent === longName);
    expect(longLabel).toBeDefined();
    expect(longLabel?.querySelectorAll("tspan")).toHaveLength(2);

    const viewBox = container.querySelector("svg")?.getAttribute("viewBox") ?? "";
    const firstNumber = Number(viewBox.split(" ")[0]);
    expect(firstNumber).toBeLessThan(-20);
  });

  it("renders a short axis label as a single tspan", () => {
    render(<FitRadar axes={[{ name: "RAG", role: 50, you: 50 }, AXES[1], AXES[2]]} size={200} />);

    const shortLabel = screen.getAllByTestId("fit-radar-axis").find((el) => el.textContent === "RAG");
    expect(shortLabel).toBeDefined();
    expect(shortLabel?.querySelectorAll("tspan")).toHaveLength(1);
  });
});
