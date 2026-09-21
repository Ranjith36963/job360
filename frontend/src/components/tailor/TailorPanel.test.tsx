import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TailorPanel } from "./TailorPanel";
import type { TailorBundle, TailoredDocOut } from "@/lib/types";

// ---------------------------------------------------------------------------
// Mocks — decision 28 (slice A): there is no generate call to mock any more.
// ---------------------------------------------------------------------------

const mockGetTailored = vi.fn();
const mockSaveTailored = vi.fn();
const mockDownloadTailored = vi.fn();

const mockGetProvenance = vi.fn();

vi.mock("@/lib/api", () => ({
  getTailored: (...args: unknown[]) => mockGetTailored(...args),
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
    text: "The CV my agent wrote",
    artifact_id: 11,
    version_no: 1,
    made_by: "agent:claude",
    updated_at: null,
    ...overrides,
  };
}

function makeBundle(overrides: Partial<TailorBundle> = {}): TailorBundle {
  return {
    job_id: 42,
    application_id: 3,
    documents: [
      makeDoc({ doc_kind: "cv", text: "The CV my agent wrote" }),
      makeDoc({ doc_kind: "cover_letter", text: "The cover letter my agent wrote", artifact_id: 12 }),
    ],
    ...overrides,
  };
}

describe("TailorPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetTailored.mockResolvedValue(makeBundle());
  });

  it("shows CV and Cover Letter tabs with the saved text seeded in", async () => {
    render(<TailorPanel jobId={42} open onOpenChange={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: /^cv$/i })).toBeInTheDocument();
    });
    expect(screen.getByRole("tab", { name: /cover letter/i })).toBeInTheDocument();
    expect(screen.getByDisplayValue("The CV my agent wrote")).toBeInTheDocument();
  });

  it("names the version and who saved it", async () => {
    render(<TailorPanel jobId={42} open onOpenChange={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByText(/v1 · saved by agent:claude/i)).toBeInTheDocument();
    });
  });

  it("offers NO generate button — the agent writes, we render (decision 28)", async () => {
    mockGetTailored.mockResolvedValue(makeBundle({ documents: [] }));
    render(<TailorPanel jobId={42} open onOpenChange={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText(/nothing saved for this job yet/i)).toBeInTheDocument();
    });
    expect(screen.queryByRole("button", { name: /generate/i })).toBeNull();
    expect(screen.queryByText(/used this month/i)).toBeNull();
  });

  it("Save calls saveTailored with the edited text for the active tab", async () => {
    mockSaveTailored.mockResolvedValue(
      makeDoc({ doc_kind: "cv", text: "Edited CV text", version_no: 2, made_by: "human" })
    );
    render(<TailorPanel jobId={42} open onOpenChange={vi.fn()} />);

    const textarea = await screen.findByDisplayValue("The CV my agent wrote");
    await userEvent.clear(textarea);
    await userEvent.type(textarea, "Edited CV text");

    await userEvent.click(screen.getByRole("button", { name: /save as new version/i }));

    await waitFor(() => {
      expect(mockSaveTailored).toHaveBeenCalledWith(42, "cv", "Edited CV text");
    });
    expect(mockToastSuccess).toHaveBeenCalled();
  });

  it("Download PDF triggers downloadTailored with pdf format for the active tab", async () => {
    mockDownloadTailored.mockResolvedValue(undefined);
    render(<TailorPanel jobId={42} open onOpenChange={vi.fn()} />);

    await screen.findByDisplayValue("The CV my agent wrote");
    await userEvent.click(screen.getByRole("button", { name: /download pdf/i }));

    await waitFor(() => {
      expect(mockDownloadTailored).toHaveBeenCalledWith(42, "cv", "pdf");
    });
  });

  it("Download DOCX triggers downloadTailored with docx format", async () => {
    mockDownloadTailored.mockResolvedValue(undefined);
    render(<TailorPanel jobId={42} open onOpenChange={vi.fn()} />);

    await screen.findByDisplayValue("The CV my agent wrote");
    await userEvent.click(screen.getByRole("button", { name: /download docx/i }));

    await waitFor(() => {
      expect(mockDownloadTailored).toHaveBeenCalledWith(42, "cv", "docx");
    });
  });

  it("Highlight my facts fetches provenance and shows grounded + added lines", async () => {
    mockGetProvenance.mockResolvedValue([
      { text: "Senior ML Engineer at Monzo", grounded: true },
      { text: "Led a team of fifty across three continents", grounded: false },
    ]);
    render(<TailorPanel jobId={42} open onOpenChange={vi.fn()} />);

    await screen.findByDisplayValue("The CV my agent wrote");
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
    expect(screen.getByText(/added on top/i)).toBeInTheDocument(); // legend present
  });

  it("surfaces a load failure instead of an empty panel", async () => {
    mockGetTailored.mockRejectedValue(new Error("boom"));
    render(<TailorPanel jobId={42} open onOpenChange={vi.fn()} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });
});
