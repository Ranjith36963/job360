import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { FoldRow, useFoldSet } from "./FoldRow";

function List({ ids }: { ids: number[] }) {
  const fold = useFoldSet();
  return (
    <>
      <button type="button" onClick={() => fold.setAll(true)}>Expand all</button>
      <button type="button" onClick={() => fold.setAll(false)}>Fold all</button>
      <ul>
        {ids.map((id) => (
          <FoldRow key={id} id={`r${id}`} testid={`row-${id}`} summaryTestid={`sum-${id}`} label={`Job ${id}`}
            open={fold.isOpen(id)} onToggle={() => fold.toggle(id)} summary={<span>Summary {id}</span>}
            action={<a href="#x" data-testid={`act-${id}`}>Open the form</a>}>
            <textarea aria-label={`Answer ${id}`} />
          </FoldRow>
        ))}
      </ul>
    </>
  );
}

describe("FoldRow", () => {
  it("starts folded, with a real + button that says it is collapsed", () => {
    render(<List ids={[1]} />);
    const toggle = screen.getByRole("button", { name: "Unfold Job 1" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveAttribute("aria-controls", "r1-body");
    expect(screen.getByLabelText("Answer 1")).not.toBeVisible();
    fireEvent.click(toggle);
    expect(screen.getByRole("button", { name: "Fold Job 1" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByLabelText("Answer 1")).toBeVisible();
  });

  it("keeps typed text through fold and unfold (the card stays mounted)", () => {
    render(<List ids={[1]} />);
    fireEvent.click(screen.getByTestId("row-1-toggle"));
    fireEvent.change(screen.getByLabelText("Answer 1"), { target: { value: "Three months" } });
    fireEvent.click(screen.getByTestId("row-1-toggle"));
    fireEvent.click(screen.getByTestId("row-1-toggle"));
    expect(screen.getByLabelText("Answer 1")).toHaveValue("Three months");
  });

  it("a click on the summary toggles; a click on the row's action does not", () => {
    render(<List ids={[1]} />);
    fireEvent.click(screen.getByText("Summary 1"));
    expect(screen.getByTestId("row-1-toggle")).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(screen.getByTestId("act-1"));
    expect(screen.getByTestId("row-1-toggle")).toHaveAttribute("aria-expanded", "true");
  });

  it("several rows can be open at once; Expand all and Fold all set every row", () => {
    render(<List ids={[1, 2, 3]} />);
    fireEvent.click(screen.getByTestId("row-1-toggle"));
    fireEvent.click(screen.getByTestId("row-2-toggle"));
    const states = () => [1, 2, 3].map((i) => screen.getByTestId(`row-${i}-toggle`).getAttribute("aria-expanded"));
    expect(states()).toEqual(["true", "true", "false"]);
    fireEvent.click(screen.getByText("Expand all"));
    expect(states()).toEqual(["true", "true", "true"]);
    fireEvent.click(screen.getByTestId("row-2-toggle"));
    expect(states()).toEqual(["true", "false", "true"]);
    fireEvent.click(screen.getByText("Fold all"));
    expect(states()).toEqual(["false", "false", "false"]);
  });
});
