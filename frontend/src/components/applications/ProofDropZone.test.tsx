import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { ProofDropZone } from "./ProofDropZone";
import type { ProofScreenshotOut, ProofStateOut } from "@/lib/api";

const m = vi.hoisted(() => ({
  getProof: vi.fn(), uploadProofScreenshot: vi.fn(), deleteProofScreenshot: vi.fn(), fetchProofScreenshot: vi.fn(),
  toastError: vi.fn(),
}));
const { getProof, uploadProofScreenshot, deleteProofScreenshot, fetchProofScreenshot, toastError } = m;
vi.mock("@/lib/api", () => m);
vi.mock("@/lib/toast", () => ({ toast: { apiError: vi.fn(), error: m.toastError } }));

function shot(id: number, deleted = false): ProofScreenshotOut {
  return {
    id, mime: "image/png", size: 10, sha256: "a", created_by: "web", created_at: "2026-10-10T00:00:00Z",
    deleted_at: deleted ? "2026-10-10T01:00:00Z" : null, delete_note: deleted ? "Deleted by you on 10 Oct 2026" : "",
  };
}
function resp(
  level: ProofStateOut["proof"]["level"], shots: ProofScreenshotOut[], max_bytes = 3 * 1024 * 1024, max_live = 3,
): ProofStateOut {
  return {
    max_bytes, max_live,
    application_id: 7,
    proof: { has_text: level === "text", has_email: level === "email", screenshots: shots.length, level },
    screenshots: shots,
  };
}
const file = (type: string, bytes: number) => new File([new Uint8Array(bytes)], "a", { type });
const mount = () => render(<ProofDropZone applicationId={7} />);
const open = (level: ProofStateOut["proof"]["level"], shots: ProofScreenshotOut[]) => {
  getProof.mockResolvedValue(resp(level, shots));
  return mount();
};

describe("ProofDropZone", () => {
  beforeEach(() => {
    [getProof, uploadProofScreenshot, deleteProofScreenshot, toastError, fetchProofScreenshot].forEach((m) => m.mockReset());
    fetchProofScreenshot.mockResolvedValue(new Blob(["x"], { type: "image/png" }));
    URL.createObjectURL = vi.fn(() => "blob:proof-1");
    URL.revokeObjectURL = vi.fn();
  });
  afterEach(() => vi.restoreAllMocks());

  it("renders the level and a blob-URL thumbnail, revoked on unmount", async () => {
    const { unmount } = open("screenshot_only", [shot(1)]);
    await waitFor(() => expect(screen.getByTestId("proof-level").textContent).toBe("Screenshot only"));
    const img = await screen.findByAltText("Proof screenshot");
    expect(img.getAttribute("src")).toBe("blob:proof-1");
    expect(fetchProofScreenshot).toHaveBeenCalledWith(7, 1);
    unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:proof-1");
  });

  it("shows 'Image unavailable' when the fetch fails", async () => {
    fetchProofScreenshot.mockRejectedValue(new Error("boom"));
    open("screenshot_only", [shot(1)]);
    await waitFor(() => expect(screen.getByTestId("proof-thumb-1").textContent).toBe("Image unavailable"));
  });

  it("rejects a 4 MB file and a text file without uploading", async () => {
    open("none", []);
    const input = await screen.findByTestId("proof-file-input");
    fireEvent.change(input, { target: { files: [file("image/png", 4 * 1024 * 1024)] } });
    expect(toastError).toHaveBeenLastCalledWith("That image is over 3 MB.");
    fireEvent.change(input, { target: { files: [file("text/plain", 10)] } });
    expect(toastError).toHaveBeenLastCalledWith("Only PNG, JPEG or WebP images.");
    expect(uploadProofScreenshot).not.toHaveBeenCalled();
  });

  it("builds the copy from the server limits, not constants", async () => {
    getProof.mockResolvedValue(resp("screenshot_only", [shot(1), shot(2)], 5 * 1024 * 1024, 2));
    mount();
    await screen.findByText(/2 of 2 screenshots/);
    cleanup();
    getProof.mockResolvedValue(resp("none", [], 5 * 1024 * 1024, 4));
    mount();
    await screen.findByText(/up to 5 MB/);
    const input = await screen.findByTestId("proof-file-input");
    fireEvent.change(input, { target: { files: [file("image/png", 6 * 1024 * 1024)] } });
    expect(toastError).toHaveBeenLastCalledWith("That image is over 5 MB.");
    cleanup();
    getProof.mockResolvedValue(resp("none", [], 2.5 * 1024 * 1024, 4));
    mount();
    await screen.findByText(/up to 2.5 MB/);
  });

  it("uploads a png then reloads", async () => {
    getProof.mockResolvedValueOnce(resp("none", [])).mockResolvedValueOnce(resp("screenshot_only", [shot(5)]));
    uploadProofScreenshot.mockResolvedValue(shot(5));
    mount();
    const input = await screen.findByTestId("proof-file-input");
    fireEvent.change(input, { target: { files: [file("image/png", 100)] } });
    await waitFor(() => expect(screen.getByTestId("proof-thumb-5")).toBeTruthy());
    expect(uploadProofScreenshot).toHaveBeenCalledWith(7, expect.any(File));
    expect(getProof).toHaveBeenCalledTimes(2);
  });

  it("hides the zone at 3 live screenshots", async () => {
    open("screenshot_only", [shot(1), shot(2), shot(3)]);
    await screen.findByText(/3 of 3 screenshots/);
    expect(screen.queryByTestId("proof-file-input")).toBeNull();
  });

  it("deletes after confirm and shows the delete note", async () => {
    getProof.mockResolvedValueOnce(resp("screenshot_only", [shot(1)])).mockResolvedValueOnce(resp("none", [shot(1, true)]));
    deleteProofScreenshot.mockResolvedValue(shot(1, true));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    mount();
    fireEvent.click(await screen.findByTestId("proof-delete-1"));
    await waitFor(() => expect(screen.getByTestId("proof-deleted-1").textContent).toBe("Deleted by you on 10 Oct 2026"));
    expect(deleteProofScreenshot).toHaveBeenCalledWith(7, 1);
    expect(screen.queryByTestId("proof-thumb-1")).toBeNull();
  });
});
