import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { CvToApprove } from "./CvToApprove";

const getCvToApprove = vi.fn();
const markCvSeen = vi.fn();
const downloadApplicationArtifact = vi.fn();
vi.mock("@/lib/api", () => ({
  getCvToApprove: (...a: unknown[]) => getCvToApprove(...a),
  markCvSeen: (...a: unknown[]) => markCvSeen(...a),
  downloadApplicationArtifact: (...a: unknown[]) => downloadApplicationArtifact(...a),
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

describe("CvToApprove", () => {
  beforeEach(() => {
    getCvToApprove.mockReset();
    markCvSeen.mockReset();
    downloadApplicationArtifact.mockReset();
    apiError.mockReset();
  });

  it("lists every CV not checked yet, with the job and a link to read it", async () => {
    getCvToApprove.mockResolvedValue({ items: [row(1), row(2, { job_title: "Analyst", job_company: "Beta" })] });
    render(<CvToApprove />);
    const first = await screen.findByTestId("approve-cv-1");
    expect(first).toHaveTextContent("AI Engineer · ElevenLabs");
    expect(first).toHaveTextContent("CV version 2");
    expect(screen.getByTestId("approve-cv-2")).toHaveTextContent("Analyst · Beta");
    expect(screen.getByTestId("approve-cv")).toHaveTextContent("Approve CV");
    expect(screen.getAllByRole("link", { name: "Read it" })[0]).toHaveAttribute("href", "/applications/1");
  });

  it("CV OK records cv_seen for that exact CV version, then the row leaves", async () => {
    getCvToApprove.mockResolvedValueOnce({ items: [row(1), row(2)] }).mockResolvedValueOnce({ items: [row(2)] });
    markCvSeen.mockResolvedValue({});
    render(<CvToApprove />);
    fireEvent.click(await screen.findByTestId("approve-cv-ok-1"));
    await waitFor(() => expect(screen.queryByTestId("approve-cv-1")).not.toBeInTheDocument());
    expect(markCvSeen).toHaveBeenCalledWith(1, 10);
    expect(screen.getByTestId("approve-cv-2")).toBeInTheDocument();
  });

  it("a failed CV OK keeps the row and says so", async () => {
    getCvToApprove.mockResolvedValue({ items: [row(1)] });
    markCvSeen.mockRejectedValue(new Error("409"));
    render(<CvToApprove />);
    fireEvent.click(await screen.findByTestId("approve-cv-ok-1"));
    await waitFor(() => expect(apiError).toHaveBeenCalled());
    expect(screen.getByTestId("approve-cv-1")).toBeInTheDocument();
  });

  it("the PDF button downloads that CV version", async () => {
    getCvToApprove.mockResolvedValue({ items: [row(3)] });
    downloadApplicationArtifact.mockResolvedValue(undefined);
    render(<CvToApprove />);
    await screen.findByTestId("approve-cv-3");
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
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(failed.container).toBeEmptyDOMElement();
  });
});
