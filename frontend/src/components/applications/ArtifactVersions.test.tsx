import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { ArtifactVersions } from "./ArtifactVersions";
import type { ApplicationArtifact, ArtifactDiff } from "@/lib/api";

const getArtifactDiff = vi.fn();
vi.mock("@/lib/api", () => ({
  getApplicationArtifact: vi.fn(),
  getArtifactDiff: (...args: unknown[]) => getArtifactDiff(...args),
}));

function artifact(id: number, version_no: number): ApplicationArtifact {
  return {
    id,
    kind: "cv",
    version_no,
    made_by: "agent",
    model: "claude",
    profile_version: 1,
    label: "",
    chars: 10,
    created_at: "2026-09-10T00:00:00Z",
    text: null,
    truncated: false,
  };
}

function diffFor(id: number, version_no: number, line: string): ArtifactDiff {
  return {
    kind: "cv",
    base: { source: "profile", artifact_id: null, version_no: null, label: "Original CV" },
    target: { artifact_id: id, version_no, made_by: "agent", model: "claude", created_at: "2026-09-10T00:00:00Z", applied: false },
    lines: [{ op: "add", text: line }],
    added: 1,
    removed: 0,
    truncated: false,
  };
}

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

describe("ArtifactVersions — Compare", () => {
  beforeEach(() => getArtifactDiff.mockReset());

  it("drops a slow response for the version the user has already moved away from", async () => {
    const v1 = artifact(901, 1);
    const v2 = artifact(902, 2);
    const slowV2 = deferred<ArtifactDiff>();
    const fastV1 = deferred<ArtifactDiff>();
    getArtifactDiff.mockImplementation((_app: number, id: number) =>
      id === 902 ? slowV2.promise : fastV1.promise
    );

    render(<ArtifactVersions applicationId={1} artifacts={[v1, v2]} />);
    const rows = screen.getAllByTestId("artifact-version");
    const compareButtons = screen.getAllByTestId("artifact-compare");

    fireEvent.click(compareButtons[1]); // Compare v2 (slow)
    fireEvent.click(compareButtons[0]); // then Compare v1 (fast) before v2 lands

    fastV1.resolve(diffFor(901, 1, "from v1"));
    await waitFor(() => expect(within(rows[0]).getByText("from v1")).toBeInTheDocument());

    slowV2.resolve(diffFor(902, 2, "from v2")); // stale — must be ignored
    await new Promise((r) => setTimeout(r, 0));

    expect(within(rows[0]).getByText("from v1")).toBeInTheDocument();
    expect(screen.queryByText("from v2")).toBeNull();
    expect(screen.queryByText("Comparing…")).toBeNull();
  });

  it("marks the version a receipt names as applied and offers no Keep", () => {
    render(
      <ArtifactVersions
        applicationId={1}
        artifacts={[artifact(901, 1), artifact(902, 2)]}
        receipts={[
          { id: 5, sent_at: "2026-09-10T00:00:00Z", channel: "", confirmation: "", cv_artifact_id: 902, cover_letter_artifact_id: null, note: "" },
        ]}
      />
    );
    const badges = screen.getAllByTestId("artifact-applied-badge");
    expect(badges).toHaveLength(1);
    expect(screen.queryByRole("button", { name: /keep/i })).toBeNull();
  });
  it("shows the assistant's ATS score with its notes and says whose opinion it is", () => {
    render(
      <ArtifactVersions
        applicationId={1}
        artifacts={[{ ...artifact(901, 1), made_by: "agent:Claude", ats_score: 82, ats_notes: "Add the keyword Kubernetes." }]}
      />
    );
    const ats = screen.getByTestId("artifact-ats");
    expect(ats).toHaveTextContent("ATS 82");
    expect(ats).toHaveTextContent("Claude's opinion");
    const details = ats.querySelector("details");
    expect(details).not.toBeNull();
    expect(details).not.toHaveAttribute("open");
    fireEvent.click(screen.getByText("ATS 82"));
    expect(screen.getByText("Add the keyword Kubernetes.")).toBeInTheDocument();
  });

  it("shows a score with no notes as a plain line, and a generic owner for a non-agent maker", () => {
    render(<ArtifactVersions applicationId={1} artifacts={[{ ...artifact(901, 1), ats_score: 40, ats_notes: null }]} />);
    const ats = screen.getByTestId("artifact-ats");
    expect(ats.querySelector("details")).toBeNull();
    expect(ats).toHaveTextContent("your assistant's opinion");
  });

  it("shows nothing when there is no ATS score", () => {
    render(<ArtifactVersions applicationId={1} artifacts={[{ ...artifact(901, 1), ats_score: null, ats_notes: "stray" }]} />);
    expect(screen.queryByTestId("artifact-ats")).toBeNull();
    expect(screen.queryByText(/ATS/)).toBeNull();
  });
});
