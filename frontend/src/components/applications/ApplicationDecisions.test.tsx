import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ApplicationDecisions } from "./ApplicationDecisions";
import { CvSeenButton } from "./CvSeenButton";
import { ArtifactVersions } from "./ArtifactVersions";
import type { ApplicationArtifact, ApplicationControls } from "@/lib/api";

const markCvSeen = vi.fn();
const approveSend = vi.fn();
const declineSend = vi.fn();
const setAutofill = vi.fn();
const clearDuplicate = vi.fn();
vi.mock("@/lib/api", () => ({
  markCvSeen: (...a: unknown[]) => markCvSeen(...a),
  approveSend: (...a: unknown[]) => approveSend(...a),
  declineSend: (...a: unknown[]) => declineSend(...a),
  setAutofill: (...a: unknown[]) => setAutofill(...a),
  clearDuplicate: (...a: unknown[]) => clearDuplicate(...a),
  getApplicationArtifact: vi.fn(),
  getArtifactDiff: vi.fn(),
  downloadApplicationArtifact: vi.fn(),
}));
vi.mock("@/lib/toast", () => ({
  toast: { success: vi.fn(), apiError: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

const MARK = { by: "web", where: "web", at: "2026-10-08T10:00:00Z" };

function controls(over: Partial<ApplicationControls> = {}): ApplicationControls {
  return {
    application_id: 7,
    cv: { artifact_id: 11, version: 2, sha256: "abc", seen: null, approved: null },
    declined: null,
    autofill: { mode: "unset", by: null, where: null, at: null },
    duplicate: { same_job: null, same_company_30d: 0, flag: "", cleared: null },
    ...over,
  };
}

function cvArtifact(id: number, version_no: number): ApplicationArtifact {
  return {
    id, kind: "cv", version_no, made_by: "agent:Claude", model: "claude", profile_version: 1, label: "",
    chars: 10, created_at: "2026-10-08T00:00:00Z", text: null, truncated: false,
  } as ApplicationArtifact;
}

describe("cv-seen-button", () => {
  beforeEach(() => markCvSeen.mockReset());

  it("shows the button for the latest CV and sends the click to the server", async () => {
    const next = controls({ cv: { artifact_id: 11, version: 2, sha256: "abc", seen: MARK, approved: null } });
    markCvSeen.mockResolvedValue(next);
    const onChanged = vi.fn();
    render(<CvSeenButton applicationId={7} artifactId={11} seen={null} onChanged={onChanged} />);
    fireEvent.click(screen.getByTestId("cv-seen-button"));
    await waitFor(() => expect(markCvSeen).toHaveBeenCalledWith(7, 11));
    expect(onChanged).toHaveBeenCalledWith(next);
  });

  it("reads 'Checked' with the date once seen, and the button is gone", () => {
    render(<CvSeenButton applicationId={7} artifactId={11} seen={MARK} />);
    expect(screen.queryByTestId("cv-seen-button")).toBeNull();
    expect(screen.getByTestId("cv-seen-done").textContent).toMatch(/Checked ✓ .*2026/);
    expect(screen.getByTestId("cv-seen-done").getAttribute("title")).toBe("on the website");
  });

  it("appears on the LATEST CV version only, and a new version shows it again", () => {
    const { rerender } = render(
      <ArtifactVersions
        applicationId={7}
        artifacts={[cvArtifact(10, 1), cvArtifact(11, 2)]}
        controls={controls({ cv: { artifact_id: 11, version: 2, sha256: "a", seen: MARK, approved: null } })}
      />
    );
    expect(screen.queryAllByTestId("cv-seen-button")).toHaveLength(0);
    expect(screen.getAllByTestId("cv-seen-done")).toHaveLength(1);
    rerender(
      <ArtifactVersions
        applicationId={7}
        artifacts={[cvArtifact(10, 1), cvArtifact(11, 2), cvArtifact(12, 3)]}
        controls={controls({ cv: { artifact_id: 12, version: 3, sha256: "b", seen: null, approved: null } })}
      />
    );
    expect(screen.getAllByTestId("cv-seen-button")).toHaveLength(1);
    expect(screen.queryByTestId("cv-seen-done")).toBeNull();
  });

  it("without controls the documents list shows no decision button at all", () => {
    render(<ArtifactVersions applicationId={7} artifacts={[cvArtifact(11, 2)]} />);
    expect(screen.queryByTestId("cv-seen-button")).toBeNull();
  });
});

describe("Send this one / Don't send", () => {
  beforeEach(() => {
    approveSend.mockReset();
    declineSend.mockReset();
  });

  it("Send records the yes and shows the state with who, where and when", async () => {
    const after = controls({ cv: { artifact_id: 11, version: 2, sha256: "abc", seen: MARK, approved: MARK } });
    approveSend.mockResolvedValue(after);
    const onChanged = vi.fn();
    const { rerender } = render(<ApplicationDecisions applicationId={7} controls={controls()} onChanged={onChanged} />);
    expect(screen.getByTestId("send-state").textContent).toMatch(/No answer yet for v2/);
    fireEvent.click(screen.getByTestId("send-approve-button"));
    await waitFor(() => expect(approveSend).toHaveBeenCalledWith(7));
    expect(onChanged).toHaveBeenCalledWith(after);
    rerender(<ApplicationDecisions applicationId={7} controls={after} onChanged={onChanged} />);
    expect(screen.getByTestId("send-state").textContent).toMatch(/Yes to v2 - on the website · .*2026/);
  });

  it("Don't send records the no and the state says so", async () => {
    const after = controls({ declined: MARK });
    declineSend.mockResolvedValue(after);
    const onChanged = vi.fn();
    const { rerender } = render(<ApplicationDecisions applicationId={7} controls={controls()} onChanged={onChanged} />);
    fireEvent.click(screen.getByTestId("send-decline-button"));
    await waitFor(() => expect(declineSend).toHaveBeenCalledWith(7));
    rerender(<ApplicationDecisions applicationId={7} controls={after} onChanged={onChanged} />);
    expect(screen.getByTestId("send-state").textContent).toMatch(/^Don't send - on the website/);
  });

  it("a chat decision shows where and which assistant", () => {
    const chat = { by: "token:claude-code", where: "chat", at: "2026-10-08T10:00:00Z" };
    render(
      <ApplicationDecisions
        applicationId={7}
        controls={controls({ declined: chat })}
        onChanged={() => undefined}
      />
    );
    expect(screen.getByTestId("send-state").textContent).toMatch(/in chat \(claude-code\)/);
  });

  it("hides the send buttons when no CV is saved yet", () => {
    render(<ApplicationDecisions applicationId={7} controls={controls({ cv: null })} onChanged={() => undefined} />);
    expect(screen.queryByTestId("send-approve-button")).toBeNull();
    expect(screen.queryByTestId("send-decline-button")).toBeNull();
    expect(screen.getByTestId("autofill-allow-button")).toBeTruthy();
  });
});

describe("Autofill / Don't autofill", () => {
  beforeEach(() => setAutofill.mockReset());

  it("unset says the assistant follows its own app permission", () => {
    render(<ApplicationDecisions applicationId={7} controls={controls()} onChanged={() => undefined} />);
    expect(screen.getByTestId("autofill-state").textContent).toMatch(/not set - your assistant follows its own app permission/);
  });

  it("each button sends its mode", async () => {
    setAutofill.mockResolvedValue(controls());
    render(<ApplicationDecisions applicationId={7} controls={controls()} onChanged={() => undefined} />);
    fireEvent.click(screen.getByTestId("autofill-allow-button"));
    await waitFor(() => expect(setAutofill).toHaveBeenLastCalledWith(7, "allow"));
    fireEvent.click(screen.getByTestId("autofill-deny-button"));
    await waitFor(() => expect(setAutofill).toHaveBeenLastCalledWith(7, "deny"));
  });

  it("the state shows the choice with who and when", () => {
    render(
      <ApplicationDecisions
        applicationId={7}
        controls={controls({ autofill: { mode: "deny", by: "token:claude-code", where: "chat", at: MARK.at } })}
        onChanged={() => undefined}
      />
    );
    expect(screen.getByTestId("autofill-state").textContent).toMatch(/not allowed - in chat \(claude-code\) · .*2026/);
  });
});

describe("Not a duplicate, go ahead", () => {
  beforeEach(() => clearDuplicate.mockReset());

  it("shows only when a duplicate is flagged, and clears it", async () => {
    const { rerender } = render(
      <ApplicationDecisions applicationId={7} controls={controls()} onChanged={() => undefined} />
    );
    expect(screen.queryByTestId("duplicate-clear-button")).toBeNull();
    const flagged = controls({
      duplicate: {
        same_job: { application_id: 3, status: "applied", applied_at: "2026-09-01T00:00:00Z" },
        same_company_30d: 0, flag: "same_job", cleared: null,
      },
    });
    const cleared = controls({ duplicate: { ...flagged.duplicate, cleared: MARK } });
    clearDuplicate.mockResolvedValue(cleared);
    const onChanged = vi.fn();
    rerender(<ApplicationDecisions applicationId={7} controls={flagged} onChanged={onChanged} />);
    expect(screen.getByTestId("duplicate-warning").textContent).toMatch(/already applied to this job on .*2026.*applied/);
    fireEvent.click(screen.getByTestId("duplicate-clear-button"));
    await waitFor(() => expect(clearDuplicate).toHaveBeenCalledWith(7));
    expect(onChanged).toHaveBeenCalledWith(cleared);
    rerender(<ApplicationDecisions applicationId={7} controls={cleared} onChanged={onChanged} />);
    expect(screen.queryByTestId("duplicate-clear-button")).toBeNull();
    expect(screen.getByTestId("duplicate-cleared").textContent).toMatch(/Not a duplicate - on the website/);
  });

  it("a same-company flag names the count", () => {
    render(
      <ApplicationDecisions
        applicationId={7}
        controls={controls({
          duplicate: { same_job: null, same_company_30d: 2, flag: "same_company", cleared: null },
        })}
        onChanged={() => undefined}
      />
    );
    expect(screen.getByTestId("duplicate-warning").textContent).toMatch(/2 other applications at this company in the last 30 days/);
  });
});
