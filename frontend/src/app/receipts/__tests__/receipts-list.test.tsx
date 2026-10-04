import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import ReceiptsPage from "../page";

const listReceipts = vi.fn();

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/api", () => ({
  listReceipts: (...args: unknown[]) => listReceipts(...args),
}));
vi.mock("@/lib/toast", () => ({
  toast: { apiError: vi.fn() },
}));

const ROW = {
  id: 7,
  job_title: "Staff Engineer",
  job_company: "Acme",
  job_location: "Remote",
  sent_at: "2026-09-20T10:30:00Z",
  channel: "email",
  note: "",
  has_cv: true,
  has_cover_letter: false,
};

beforeEach(() => {
  listReceipts.mockReset();
});

describe("/receipts list", () => {
  it("renders one linked row per receipt with the sent date and facts", async () => {
    listReceipts.mockResolvedValue({ receipts: [ROW] });
    render(<ReceiptsPage />);

    expect(screen.getByRole("heading", { level: 1, name: "Receipts" })).toBeInTheDocument();
    const list = await screen.findByTestId("receipts-list");
    expect(list).toHaveTextContent("Staff Engineer");
    expect(list).toHaveTextContent("Acme · Remote");
    expect(list).toHaveTextContent(/Sent .*2026/);
    expect(list).toHaveTextContent("CV kept");
    expect(list).toHaveTextContent("No cover letter");
    expect(list).toHaveTextContent("via Email");
    expect(screen.getByRole("link", { name: /Staff Engineer/ })).toHaveAttribute("href", "/receipts/7");
  });

  it("shows the empty state with the Jobs in link", async () => {
    listReceipts.mockResolvedValue({ receipts: [] });
    render(<ReceiptsPage />);

    expect(await screen.findByText("No receipts yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Jobs in/ })).toHaveAttribute("href", "/bring");
    expect(screen.queryByTestId("receipts-list")).not.toBeInTheDocument();
  });
});
