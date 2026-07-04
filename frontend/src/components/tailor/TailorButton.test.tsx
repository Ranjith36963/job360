import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TailorButton } from "./TailorButton";

// TailorButton always mounts TailorPanel (dialog closed by default), which
// calls getTailored only once opened — mock the API module so an open click
// resolves cleanly instead of hitting the network.
vi.mock("@/lib/api", () => ({
  getTailored: vi.fn().mockResolvedValue({
    job_id: 42,
    documents: [],
    quota_used: 0,
    quota_limit: 10,
  }),
  generateTailored: vi.fn(),
  saveTailored: vi.fn(),
  downloadTailoredPdf: vi.fn(),
}));

describe("TailorButton", () => {
  it("renders the 'Tailor my CV' button", () => {
    render(<TailorButton jobId={42} />);
    expect(
      screen.getByRole("button", { name: /tailor my cv/i })
    ).toBeInTheDocument();
  });

  it("opens the TailorPanel dialog on click", async () => {
    render(<TailorButton jobId={42} />);
    await userEvent.click(screen.getByRole("button", { name: /tailor my cv/i }));

    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });
    // Dialog title also reads "Tailor my CV" — confirms the panel content mounted.
    expect(
      screen.getByRole("heading", { name: /tailor my cv/i })
    ).toBeInTheDocument();
  });

  it("does not open the panel until clicked", () => {
    render(<TailorButton jobId={42} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
