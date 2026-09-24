import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TailorSection } from "./TailorSection";

// The panel fetches on open — mock the API so opening it doesn't hit the network.
const mockGetTailored = vi.fn();
vi.mock("@/lib/api", () => ({
  getTailored: (...args: unknown[]) => mockGetTailored(...args),
  saveTailored: vi.fn(),
  downloadTailored: vi.fn(),
  getTailoredProvenance: vi.fn(),
}));
vi.mock("@/lib/toast", () => ({
  toast: { success: vi.fn(), error: vi.fn(), apiError: vi.fn() },
}));

describe("TailorSection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetTailored.mockResolvedValue({
      job_id: 7,
      application_id: 3,
      documents: [],
    });
  });

  it("tells the user the sentence that makes their own assistant write the CV", () => {
    render(<TailorSection jobId={7} applicationId={3} />);
    expect(screen.getByText(/ask your assistant/i)).toBeInTheDocument();
    expect(
      screen.getByText(/write a tailored CV for application 3 and save it/i)
    ).toBeInTheDocument();
  });

  it("links to the connect page", () => {
    render(<TailorSection jobId={7} applicationId={3} />);
    expect(screen.getByRole("link", { name: /connect your assistant/i })).toHaveAttribute(
      "href",
      "/settings/connect"
    );
  });

  it("offers NO way to generate a document — Job360 has no LLM (decision 28)", () => {
    render(<TailorSection jobId={7} applicationId={3} hasDocuments />);
    expect(screen.queryByRole("button", { name: /tailor my cv/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /generate/i })).toBeNull();
  });

  it("opens the saved documents only once something is saved", async () => {
    const { rerender } = render(<TailorSection jobId={7} applicationId={3} />);
    expect(screen.queryByRole("button", { name: /edit & download/i })).toBeNull();

    rerender(<TailorSection jobId={7} applicationId={3} hasDocuments />);
    await userEvent.click(screen.getByRole("button", { name: /edit & download/i }));
    await waitFor(() => expect(mockGetTailored).toHaveBeenCalledWith(7));
  });
});
