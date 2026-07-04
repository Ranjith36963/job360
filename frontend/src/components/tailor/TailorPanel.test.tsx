import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TailorPanel } from "./TailorPanel";
import { ApiError } from "@/lib/api-error";
import type { TailorBundle, TailoredDocOut } from "@/lib/types";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const mockGetTailored = vi.fn();
const mockGenerateTailored = vi.fn();
const mockSaveTailored = vi.fn();
const mockDownloadTailored = vi.fn();

const mockGetProvenance = vi.fn();

vi.mock("@/lib/api", () => ({
  getTailored: (...args: unknown[]) => mockGetTailored(...args),
  generateTailored: (...args: unknown[]) => mockGenerateTailored(...args),
  saveTailored: (...args: unknown[]) => mockSaveTailored(...args),
  downloadTailored: (...args: unknown[]) => mockDownloadTailored(...args),
  getTailoredProvenance: (...args: unknown[]) => mockGetProvenance(...args),
}));

const mockToastSuccess = vi.fn();
const mockToastError = vi.fn();
const mockToastApiError = vi.fn();

vi.mock("@/lib/toast", () => ({
  toast: {
    success: (...args: unknown[]) => mockToastSuccess(...args),
    error: (...args: unknown[]) => mockToastError(...args),
    apiError: (...args: unknown[]) => mockToastApiError(...args),
    info: vi.fn(),
  },
}));

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeDoc(overrides: Partial<TailoredDocOut> = {}): TailoredDocOut {
  return {
    doc_kind: "cv",
    ai_draft: "AI CV draft text",
    polished: null,
    status: "draft",
    model: "cerebras",
    updated_at: null,
    ...overrides,
  };
}

function makeBundle(overrides: Partial<TailorBundle> = {}): TailorBundle {
  return {
    job_id: 42,
    documents: [
      makeDoc({ doc_kind: "cv", ai_draft: "AI CV draft text" }),
      makeDoc({ doc_kind: "cover_letter", ai_draft: "AI cover letter draft" }),
    ],
    quota_used: 1,
    quota_limit: 10,
    ...overrides,
  };
}

describe("TailorPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetTailored.mockResolvedValue(makeBundle());
  });

  it("shows CV and Cover Letter tabs with the AI draft seeded in", async () => {
    render(<TailorPanel jobId={42} open onOpenChange={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: /^cv$/i })).toBeInTheDocument();
    });
    expect(screen.getByRole("tab", { name: /cover letter/i })).toBeInTheDocument();
    expect(screen.getByDisplayValue("AI CV draft text")).toBeInTheDocument();
  });

  it("shows the quota usage", async () => {
    render(<TailorPanel jobId={42} open onOpenChange={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByText("1/10 used this month")).toBeInTheDocument();
    });
  });

  it("offers Generate when no documents exist yet", async () => {
    mockGetTailored.mockResolvedValue(makeBundle({ documents: [] }));
    render(<TailorPanel jobId={42} open onOpenChange={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^generate$/i })).toBeInTheDocument();
    });
    expect(screen.queryByDisplayValue(/AI CV draft/)).not.toBeInTheDocument();
  });

  it("Save calls saveTailored with the edited text for the active tab", async () => {
    mockSaveTailored.mockResolvedValue(
      makeDoc({ doc_kind: "cv", polished: "Edited CV text" })
    );
    render(<TailorPanel jobId={42} open onOpenChange={vi.fn()} />);

    const textarea = await screen.findByDisplayValue("AI CV draft text");
    await userEvent.clear(textarea);
    await userEvent.type(textarea, "Edited CV text");

    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(mockSaveTailored).toHaveBeenCalledWith(42, "cv", "Edited CV text");
    });
    expect(mockToastSuccess).toHaveBeenCalled();
  });

  it("Download PDF triggers downloadTailored with pdf format for the active tab", async () => {
    mockDownloadTailored.mockResolvedValue(undefined);
    render(<TailorPanel jobId={42} open onOpenChange={vi.fn()} />);

    await screen.findByDisplayValue("AI CV draft text");
    await userEvent.click(screen.getByRole("button", { name: /download pdf/i }));

    await waitFor(() => {
      expect(mockDownloadTailored).toHaveBeenCalledWith(42, "cv", "pdf");
    });
  });

  it("Download DOCX triggers downloadTailored with docx format", async () => {
    mockDownloadTailored.mockResolvedValue(undefined);
    render(<TailorPanel jobId={42} open onOpenChange={vi.fn()} />);

    await screen.findByDisplayValue("AI CV draft text");
    await userEvent.click(screen.getByRole("button", { name: /download docx/i }));

    await waitFor(() => {
      expect(mockDownloadTailored).toHaveBeenCalledWith(42, "cv", "docx");
    });
  });

  it("Highlight my facts fetches provenance and shows grounded + AI-added lines", async () => {
    mockGetProvenance.mockResolvedValue([
      { text: "Senior ML Engineer at Monzo", grounded: true },
      { text: "Led a team of fifty across three continents", grounded: false },
    ]);
    render(<TailorPanel jobId={42} open onOpenChange={vi.fn()} />);

    await screen.findByDisplayValue("AI CV draft text");
    await userEvent.click(
      screen.getByRole("button", { name: /highlight my facts/i })
    );

    await waitFor(() =>
      expect(mockGetProvenance).toHaveBeenCalledWith(42, "cv")
    );
    expect(
      await screen.findByText(/senior ml engineer at monzo/i)
    ).toBeInTheDocument();
    expect(screen.getByText(/led a team of fifty/i)).toBeInTheDocument();
    expect(screen.getByText(/ai-added/i)).toBeInTheDocument(); // legend present
  });

  it("shows a limit-reached toast on 402 from Generate", async () => {
    mockGetTailored.mockResolvedValue(makeBundle({ documents: [] }));
    mockGenerateTailored.mockRejectedValue(
      new ApiError(
        402,
        "Monthly free limit reached (10/10). Tailored documents are a premium feature."
      )
    );
    render(<TailorPanel jobId={42} open onOpenChange={vi.fn()} />);

    const generateBtn = await screen.findByRole("button", { name: /^generate$/i });
    await userEvent.click(generateBtn);

    await waitFor(() => {
      expect(mockToastError).toHaveBeenCalledWith(
        expect.stringMatching(/monthly free limit reached/i)
      );
    });
  });

  it("shows an 'upload your CV' toast on 400 from Generate", async () => {
    mockGetTailored.mockResolvedValue(makeBundle({ documents: [] }));
    mockGenerateTailored.mockRejectedValue(new ApiError(400, "No CV on file"));
    render(<TailorPanel jobId={42} open onOpenChange={vi.fn()} />);

    const generateBtn = await screen.findByRole("button", { name: /^generate$/i });
    await userEvent.click(generateBtn);

    await waitFor(() => {
      expect(mockToastError).toHaveBeenCalledWith(
        expect.stringMatching(/upload your cv/i)
      );
    });
  });
});
