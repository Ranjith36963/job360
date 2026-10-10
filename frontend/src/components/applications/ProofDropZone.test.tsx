import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ProofDropZone } from "./ProofDropZone";
import type { ProofScreenshotOut, ProofStateOut } from "@/lib/api";

const getProof = vi.fn();
const uploadProofScreenshot = vi.fn();
const deleteProofScreenshot = vi.fn();
const fetchProofScreenshot = vi.fn();
vi.mock("@/lib/api", () => ({
  getProof: (...a: unknown[]) => getProof(...a),
  uploadProofScreenshot: (...a: unknown[]) => uploadProofScreenshot(...a),
  deleteProofScreenshot: (...a: unknown[]) => deleteProofScreenshot(...a),
  fetchProofScreenshot: (...a: unknown[]) => fetchProofScreenshot(...a),
}));
const toastError = vi.fn();
vi.mock("@/lib/toast", () => ({
  toast: { success: vi.fn(), apiError: vi.fn(), error: (...a: unknown[]) => toastError(...a), info: vi.fn() },
}));

function shot(id: number, deleted = false): ProofScreenshotOut {
  return {
    id, mime: "image/png", size: 10, sha256: "a", created_by: "web", created_at: "2026-10-10T00:00:00Z",
    deleted_at: deleted ? "2026-10-10T01:00:00Z" : null, delete_note: deleted ? "Deleted by you on 10 Oct" : "",
  };
}
function resp(level: ProofStateOut["proof"]["level"], shots: ProofScreenshotOut[]): ProofStateOut {
  return {
    application_id: 7,
    proof: { has_text: level === "text", has_email: level === "email", screenshots: shots.length, level },
    screenshots: shots,
  };
}
function file(type: string, bytes: number, name = "a.png") {
  return new File([new Uint8Array(bytes)], name, { type });
}

describe("ProofDropZone", () => {
  beforeEach(() => {
    getProof.mockReset();
    uploadProofScreenshot.mockReset();
    deleteProofScreenshot.mockReset();
    toastError.mockReset();
    fetchProofScreenshot.mockReset();
    fetchProofScreenshot.mockResolvedValue(new Blob(["x"], { type: "image/png" }));
    URL.createObjectURL = vi.fn(() => "blob:proof-1");
    URL.revokeObjectURL = vi.fn();
  });
  afterEach(() => vi.restoreAllMocks());

  it("renders the proof level as plain text", async () => {
    getProof.mockResolvedValue(resp("screenshot_only", [shot(1)]));
    render(<ProofDropZone applicationId={7} />);
    await waitFor(() => expect(screen.getByTestId("proof-level").textContent).toBe("Screenshot only"));
    const img = await screen.findByAltText("Proof screenshot");
    expect(img.getAttribute("src")).toBe("blob:proof-1");
    expect(fetchProofScreenshot).toHaveBeenCalledWith(7, 1);
  });

  it("revokes the blob URL on unmount", async () => {
    getProof.mockResolvedValue(resp("screenshot_only", [shot(1)]));
    const { unmount } = render(<ProofDropZone applicationId={7} />);
    await screen.findByAltText("Proof screenshot");
    unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:proof-1");
  });

  it("shows 'Image unavailable' when the fetch fails", async () => {
    getProof.mockResolvedValue(resp("screenshot_only", [shot(1)]));
    fetchProofScreenshot.mockRejectedValue(new Error("boom"));
    render(<ProofDropZone applicationId={7} />);
    await waitFor(() => expect(screen.getByTestId("proof-thumb-1").textContent).toBe("Image unavailable"));
  });

  it("rejects a 4 MB file and a text file without uploading", async () => {
    getProof.mockResolvedValue(resp("none", []));
    render(<ProofDropZone applicationId={7} />);
    const input = await screen.findByTestId("proof-file-input");
    fireEvent.change(input, { target: { files: [file("image/png", 4 * 1024 * 1024)] } });
    expect(toastError).toHaveBeenLastCalledWith("That image is over 3 MB.");
    fireEvent.change(input, { target: { files: [file("text/plain", 10, "a.txt")] } });
    expect(toastError).toHaveBeenLastCalledWith("Only PNG, JPEG or WebP images.");
    expect(uploadProofScreenshot).not.toHaveBeenCalled();
  });

  it("uploads a png then reloads", async () => {
    getProof.mockResolvedValueOnce(resp("none", [])).mockResolvedValueOnce(resp("screenshot_only", [shot(5)]));
    uploadProofScreenshot.mockResolvedValue(shot(5));
    render(<ProofDropZone applicationId={7} />);
    const input = await screen.findByTestId("proof-file-input");
    fireEvent.change(input, { target: { files: [file("image/png", 100)] } });
    await waitFor(() => expect(screen.getByTestId("proof-thumb-5")).toBeTruthy());
    expect(uploadProofScreenshot).toHaveBeenCalledWith(7, expect.any(File));
    expect(getProof).toHaveBeenCalledTimes(2);
  });

  it("hides the zone at 3 live screenshots", async () => {
    getProof.mockResolvedValue(resp("screenshot_only", [shot(1), shot(2), shot(3)]));
    render(<ProofDropZone applicationId={7} />);
    await screen.findByText(/3 of 3 screenshots/);
    expect(screen.queryByTestId("proof-file-input")).toBeNull();
  });

  it("deletes after confirm and shows the delete note", async () => {
    getProof.mockResolvedValueOnce(resp("screenshot_only", [shot(1)])).mockResolvedValueOnce(resp("none", [shot(1, true)]));
    deleteProofScreenshot.mockResolvedValue(shot(1, true));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<ProofDropZone applicationId={7} />);
    fireEvent.click(await screen.findByTestId("proof-delete-1"));
    await waitFor(() => expect(screen.getByTestId("proof-deleted-1").textContent).toBe("Deleted by you on 10 Oct"));
    expect(deleteProofScreenshot).toHaveBeenCalledWith(7, 1);
    expect(screen.queryByTestId("proof-thumb-1")).toBeNull();
  });
});
