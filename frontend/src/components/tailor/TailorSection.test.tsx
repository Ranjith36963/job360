import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TailorSection } from "./TailorSection";

// The panel fetches on open — mock the API so opening it doesn't hit the network.
const mockGetTailored = vi.fn();
vi.mock("@/lib/api", () => ({
  getTailored: (...args: unknown[]) => mockGetTailored(...args),
  generateTailored: vi.fn(),
  saveTailored: vi.fn(),
  downloadTailored: vi.fn(),
}));
vi.mock("@/lib/toast", () => ({
  toast: { success: vi.fn(), error: vi.fn(), apiError: vi.fn() },
}));

describe("TailorSection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetTailored.mockResolvedValue({
      job_id: 7,
      documents: [],
      quota_used: 0,
      quota_limit: 10,
    });
  });

  it("renders the prominent AI heading", () => {
    render(<TailorSection jobId={7} />);
    expect(
      screen.getByText(/tailor my ats-friendly cv using ai/i)
    ).toBeInTheDocument();
  });

  it("offers TWO separate entry points — CV and Cover Letter", () => {
    render(<TailorSection jobId={7} />);
    expect(screen.getByRole("button", { name: /tailor my cv/i })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /create cover letter/i })
    ).toBeInTheDocument();
  });

  it("opens the editor when 'Tailor my CV' is clicked", async () => {
    render(<TailorSection jobId={7} />);
    await userEvent.click(screen.getByRole("button", { name: /tailor my cv/i }));
    await waitFor(() => expect(mockGetTailored).toHaveBeenCalledWith(7));
  });

  it("opens the editor when 'Create Cover Letter' is clicked", async () => {
    render(<TailorSection jobId={7} />);
    await userEvent.click(
      screen.getByRole("button", { name: /create cover letter/i })
    );
    await waitFor(() => expect(mockGetTailored).toHaveBeenCalledWith(7));
  });
});
