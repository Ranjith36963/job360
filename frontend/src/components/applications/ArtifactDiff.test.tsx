import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { ArtifactDiff } from "./ArtifactDiff";
import type { ArtifactDiff as ArtifactDiffData } from "@/lib/api";

function makeDiff(overrides: Partial<ArtifactDiffData> = {}): ArtifactDiffData {
  return {
    kind: "cv",
    base: { source: "profile", artifact_id: null, version_no: null, label: "Original CV" },
    target: {
      artifact_id: 42,
      version_no: 2,
      made_by: "agent",
      model: "claude",
      created_at: "2026-09-11T10:00:00+00:00",
      applied: false,
    },
    lines: [
      { op: "equal", text: "Jane Doe" },
      { op: "del", text: "Python, Postgres" },
      { op: "add", text: "Python, Postgres, Kubernetes" },
      { op: "equal", text: "Built fraud models at Acme." },
    ],
    added: 1,
    removed: 1,
    truncated: false,
    ...overrides,
  };
}

describe("ArtifactDiff", () => {
  it("paints the removed line on the left pane and the added line on the right, unchanged on both", () => {
    render(<ArtifactDiff diff={makeDiff()} />);
    const left = screen.getByTestId("artifact-diff-left");
    const right = screen.getByTestId("artifact-diff-right");

    expect(within(left).getByText("Python, Postgres")).toHaveAttribute("data-diff", "del");
    expect(within(left).queryByText("Python, Postgres, Kubernetes")).toBeNull();

    expect(within(right).getByText("Python, Postgres, Kubernetes")).toHaveAttribute("data-diff", "add");
    expect(within(right).queryByText("Python, Postgres")).toBeNull();

    expect(within(left).getByText("Jane Doe")).toHaveAttribute("data-diff", "equal");
    expect(within(right).getByText("Jane Doe")).toHaveAttribute("data-diff", "equal");
  });

  it("names the applied version and never offers a Keep button", () => {
    render(<ArtifactDiff diff={makeDiff({ target: { ...makeDiff().target, applied: true } })} />);
    expect(screen.getByText("Applied version")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /keep/i })).toBeNull();
    expect(screen.getByTestId("artifact-diff-added")).toHaveTextContent("1 added");
    expect(screen.getByTestId("artifact-diff-removed")).toHaveTextContent("1 removed");
  });

  it("says when the text was cut", () => {
    render(<ArtifactDiff diff={makeDiff({ truncated: true })} />);
    expect(screen.getByText(/showing the first part only/)).toBeInTheDocument();
  });
});
