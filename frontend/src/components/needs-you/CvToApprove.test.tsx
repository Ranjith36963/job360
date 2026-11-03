import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { JobCards } from "./JobCards";

// "Approve CV" now lives on each job's own card on Needs you (owner 2026-10-26): a job whose only
// open item is its CV still gets a card, and the CV is approved there.
const getCvToApprove = vi.fn();
const markCvSeen = vi.fn();
const downloadApplicationArtifact = vi.fn();
const fetchApplicationArtifactNamed = vi.fn();
const openPdf = vi.fn();
vi.mock("@/lib/pdf-pages", () => ({ openPdf: (...a: unknown[]) => openPdf(...a) }));
vi.mock("@/lib/api", () => ({
  fetchApplicationArtifactNamed: (...a: unknown[]) => fetchApplicationArtifactNamed(...a),
  getCvToApprove: (...a: unknown[]) => getCvToApprove(...a),
  markCvSeen: (...a: unknown[]) => markCvSeen(...a),
  downloadApplicationArtifact: (...a: unknown[]) => downloadApplicationArtifact(...a),
  getApplication: async (id: number) => ({
    id, asks: [], contacts: [], artifacts: [], form_state: "", updated_at: "2026-10-20T10:00:00+00:00",
    job: { job_company: id === 2 ? "Beta" : "ElevenLabs", job_title: id === 2 ? "Analyst" : "AI Engineer", job_url: "" },
  }),
  listPeople: async () => ({ people: [] }),
  getReadyToSend: async () => ({ paused: false, total: 0, unflagged: 0, items: [] }),
}));
const apiError = vi.fn();
vi.mock("@/lib/toast", () => ({ toast: { apiError: (...a: unknown[]) => apiError(...a) } }));

const row = (id: number, over: Record<string, unknown> = {}) => ({
  application_id: id,
  job_title: "AI Engineer",
  job_company: "ElevenLabs",
  cv: { artifact_id: id * 10, version: 2, saved_at: "2026-10-22T20:00:00Z", made_by: "agent:Claude" },
  ...over,
});
const onChanged = vi.fn(async () => {});
const CvToApprove = () => <JobCards open={[]} onChanged={onChanged} />;

describe("CvToApprove", () => {
  beforeEach(() => {
    getCvToApprove.mockReset();
    markCvSeen.mockReset();
    downloadApplicationArtifact.mockReset();
    apiError.mockReset();
    onChanged.mockClear();
  });

  it("lists every CV not checked yet, with the job and a link to read it", async () => {
    getCvToApprove.mockResolvedValue({ items: [row(1), row(2, { job_title: "Analyst", job_company: "Beta" })] });
    render(<CvToApprove />);
    const first = await screen.findByTestId("job-card-1");
    // The folded row names the job; the card inside it holds the CV.
    expect(screen.getByTestId("job-row-1")).toHaveTextContent("ElevenLabs");
    expect(first).toHaveTextContent("CV version 2, not approved yet");
    await screen.findByTestId("job-card-2");
    expect(screen.getByTestId("job-row-2")).toHaveTextContent("Beta");
    fireEvent.click(screen.getByTestId("job-row-1-toggle")); // every job row starts folded
    expect(screen.getAllByRole("button", { name: "View CV" })[0]).toBeInTheDocument();
  });

  it("View CV opens the real PDF in the page, every page of it (owner 2026-11-03)", async () => {
    getCvToApprove.mockResolvedValue({ items: [row(1)] });
    fetchApplicationArtifactNamed.mockResolvedValue({ blob: { arrayBuffer: async () => new ArrayBuffer(8) }, filename: "Ranjith_Guruprakash_CV.pdf" });
    const draw = vi.fn(() => ({ done: Promise.resolve(), cancel: vi.fn() }));
    openPdf.mockResolvedValue({ count: 2, draw, close: vi.fn() });
    render(<CvToApprove />);
    await screen.findByTestId("job-card-1");
    fireEvent.click(screen.getByTestId("job-row-1-toggle"));
    fireEvent.click(screen.getByTestId("cv-view-1"));
    expect(await screen.findByTestId("cv-preview")).toHaveTextContent("CV version 2");
    expect(await screen.findByTestId("cv-preview-page-2")).toBeInTheDocument();
    expect(fetchApplicationArtifactNamed).toHaveBeenCalledWith(1, 10, "pdf");
    // The version AND the file name the employer gets (owner, 2026-11-03).
    expect(screen.getByTestId("cv-preview-title")).toHaveTextContent(/^CV version 2/);
    expect(screen.getByTestId("cv-preview-file")).toHaveTextContent("Ranjith_Guruprakash_CV.pdf");
    await waitFor(() => expect(draw).toHaveBeenCalledTimes(2));
    expect(screen.queryByText(/employer searches/i)).not.toBeInTheDocument();
  });

  it("a CV that can't be opened says so and points at the download", async () => {
    getCvToApprove.mockResolvedValue({ items: [row(1)] });
    fetchApplicationArtifactNamed.mockRejectedValue(new Error("boom"));
    render(<CvToApprove />);
    await screen.findByTestId("job-card-1");
    fireEvent.click(screen.getByTestId("job-row-1-toggle"));
    fireEvent.click(screen.getByTestId("cv-view-1"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Use PDF to download it");
  });

  it("CV OK records cv_seen for that exact CV version, then the row leaves", async () => {
    getCvToApprove.mockResolvedValue({ items: [row(1), row(2)] });
    markCvSeen.mockResolvedValue({});
    const { rerender } = render(<CvToApprove />);
    fireEvent.click(await screen.findByTestId("cv-ok-1"));
    await waitFor(() => expect(markCvSeen).toHaveBeenCalledWith(1, 10));
    expect(onChanged).toHaveBeenCalled();
    // The page reloads its asks (a new list), and the list re-reads the CVs to check.
    getCvToApprove.mockResolvedValue({ items: [row(2)] });
    rerender(<CvToApprove />);
    await waitFor(() => expect(screen.queryByTestId("job-card-1")).not.toBeInTheDocument());
    expect(await screen.findByTestId("job-card-2")).toBeInTheDocument();
  });

  it("a failed CV OK keeps the row and says so", async () => {
    getCvToApprove.mockResolvedValue({ items: [row(1)] });
    markCvSeen.mockRejectedValue(new Error("409"));
    render(<CvToApprove />);
    fireEvent.click(await screen.findByTestId("cv-ok-1"));
    await waitFor(() => expect(apiError).toHaveBeenCalled());
    expect(screen.getByTestId("job-card-1")).toBeInTheDocument();
  });

  it("the PDF button downloads that CV version", async () => {
    getCvToApprove.mockResolvedValue({ items: [row(3)] });
    downloadApplicationArtifact.mockResolvedValue(undefined);
    render(<CvToApprove />);
    await screen.findByTestId("job-card-3");
    fireEvent.click(screen.getByTestId("job-row-3-toggle")); // every job row starts folded
    fireEvent.click(screen.getByRole("button", { name: "PDF" }));
    await waitFor(() => expect(downloadApplicationArtifact).toHaveBeenCalledWith(3, 30, "pdf", "CV_v2"));
  });

  it("shows nothing when every CV was checked, or the read fails", async () => {
    getCvToApprove.mockResolvedValueOnce({ items: [] });
    const empty = render(<CvToApprove />);
    await waitFor(() => expect(getCvToApprove).toHaveBeenCalledTimes(1));
    expect(empty.container).toBeEmptyDOMElement();
    empty.unmount();

    getCvToApprove.mockRejectedValueOnce(new Error("down"));
    const failed = render(<CvToApprove />);
    await waitFor(() => expect(getCvToApprove).toHaveBeenCalledTimes(2));
    expect(failed.container).toBeEmptyDOMElement();
  });
});
