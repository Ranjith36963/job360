import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { ArtifactVersions } from "./ArtifactVersions";
import type { ApplicationArtifact, ArtifactDiff } from "@/lib/api";

const getArtifactDiff = vi.fn();
const getApplicationArtifact = vi.fn();
const downloadApplicationArtifact = vi.fn();
const toastSuccess = vi.fn();
vi.mock("@/lib/api", () => ({
  getApplicationArtifact: (...args: unknown[]) => getApplicationArtifact(...args),
  getArtifactDiff: (...args: unknown[]) => getArtifactDiff(...args),
  downloadApplicationArtifact: (...args: unknown[]) => downloadApplicationArtifact(...args),
}));
vi.mock("@/lib/toast", () => ({
  toast: { success: (m: string) => toastSuccess(m), apiError: vi.fn(), error: vi.fn(), info: vi.fn() },
}));
const writeText = vi.fn().mockResolvedValue(undefined);
Object.assign(navigator, { clipboard: { writeText } });

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

describe("ArtifactVersions — Copy / Word / PDF", () => {
  beforeEach(() => {
    getApplicationArtifact.mockReset();
    downloadApplicationArtifact.mockReset();
    toastSuccess.mockReset();
    writeText.mockClear();
  });

  it("offers Copy, Word and PDF on every cv / cover-letter version, none on other kinds", () => {
    render(
      <ArtifactVersions
        applicationId={7}
        artifacts={[
          artifact(901, 1),
          artifact(902, 2),
          { ...artifact(903, 1), kind: "cover_letter" },
          { ...artifact(904, 1), kind: "answers" },
        ]}
      />
    );
    expect(screen.getAllByTestId("artifact-copy")).toHaveLength(3);
    expect(screen.getAllByTestId("artifact-download-docx")).toHaveLength(3);
    expect(screen.getAllByTestId("artifact-download-pdf")).toHaveLength(3);
  });

  it("Copy fetches the version's text, writes it to the clipboard and toasts", async () => {
    getApplicationArtifact.mockResolvedValue({ ...artifact(901, 1), text: "Jane Doe CV" });
    render(<ArtifactVersions applicationId={7} artifacts={[artifact(901, 1)]} />);
    fireEvent.click(screen.getByTestId("artifact-copy"));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("Jane Doe CV"));
    expect(getApplicationArtifact).toHaveBeenCalledWith(7, 901);
    await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith("Copied"));
  });

  it("Copy uses text the row already has, without a fetch", async () => {
    render(<ArtifactVersions applicationId={7} artifacts={[{ ...artifact(901, 1), text: "inline" }]} />);
    fireEvent.click(screen.getByTestId("artifact-copy"));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("inline"));
    expect(getApplicationArtifact).not.toHaveBeenCalled();
  });

  it("Word and PDF call the download api for that version, showing Downloading… meanwhile", async () => {
    const pending = deferred<void>();
    downloadApplicationArtifact.mockReturnValue(pending.promise);
    render(<ArtifactVersions applicationId={7} artifacts={[artifact(901, 1), artifact(902, 2)]} />);
    fireEvent.click(screen.getAllByTestId("artifact-download-docx")[1]);
    expect(downloadApplicationArtifact).toHaveBeenCalledWith(7, 902, "docx", "cv-v2");
    expect(screen.getByTestId("artifact-downloading")).toHaveTextContent("Downloading…");
    // every other download button is disabled while one is in flight
    expect(screen.getAllByTestId("artifact-download-pdf")[0]).toBeDisabled();
    pending.resolve();
    await waitFor(() => expect(screen.queryByTestId("artifact-downloading")).toBeNull());
    fireEvent.click(screen.getAllByTestId("artifact-download-pdf")[0]);
    expect(downloadApplicationArtifact).toHaveBeenLastCalledWith(7, 901, "pdf", "cv-v1");
  });
});
