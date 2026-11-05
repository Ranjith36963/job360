import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

const getCoveredAnswers = vi.fn();
const deleteCoveredAnswer = vi.fn();
vi.mock("@/lib/api", () => ({
  getCoveredAnswers: (...a: unknown[]) => getCoveredAnswers(...a),
  deleteCoveredAnswer: (...a: unknown[]) => deleteCoveredAnswer(...a),
  getCountryPage: vi.fn(),
}));
vi.mock("@/lib/toast", () => ({ toast: { apiError: vi.fn(), success: vi.fn() } }));

import { MemoryCoveredAnswers } from "./MemoryCoveredAnswers";

const row = (id: string, country: string | null, answer: string, memory: string | null,
  verdict: "same" | "different" | "memory_empty") => ({
  id, index: 0, question: `Do you need visa sponsorship (${id})?`, answer, country, topic: null, memory, verdict,
});
const gb = row("gb", "GB", "Yes", "No", "different");
const inRow = row("in", "IN", "No", null, "memory_empty");

beforeEach(() => {
  getCoveredAnswers.mockReset().mockResolvedValue({ answers: [gb, inRow], profile_edit_id: 7 });
  deleteCoveredAnswer.mockReset().mockResolvedValue({ deleted: "gb", profile_edit_id: 8 });
});

describe("MemoryCoveredAnswers (Memory first, plan S3)", () => {
  it("shows each saved answer beside what Memory says, in plain words, and links an empty one to Memory", async () => {
    render(<MemoryCoveredAnswers />);
    expect(await screen.findByText("Old saved answers that Memory now covers")).toBeInTheDocument();
    expect(screen.getByTestId("covered-saved-gb")).toHaveTextContent("Yes");
    expect(screen.getByTestId("covered-memory-gb")).toHaveTextContent("No");
    expect(screen.getByTestId("covered-verdict-gb")).toHaveTextContent("Says something different from Memory");
    expect(screen.queryByTestId("covered-set-gb")).toBeNull();
    expect(screen.getByTestId("covered-memory-in")).toHaveTextContent("nothing yet");
    expect(screen.getByTestId("covered-verdict-in")).toHaveTextContent("Memory has nothing for this yet");
    expect(screen.getByTestId("covered-set-in")).toHaveAttribute("href", "/profile/memory");
    expect(screen.queryByText(/memory_empty/)).toBeNull(); // no codes on the card
  });

  it("deletes one row only after Delete and then Yes, and Cancel deletes nothing", async () => {
    render(<MemoryCoveredAnswers />);
    fireEvent.click(await screen.findByTestId("covered-delete-gb"));
    expect(deleteCoveredAnswer).not.toHaveBeenCalled(); // the first click only asks
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("covered-confirm-gb")).toBeNull();
    expect(deleteCoveredAnswer).not.toHaveBeenCalled();
    getCoveredAnswers.mockResolvedValue({ answers: [inRow], profile_edit_id: 8 });
    fireEvent.click(screen.getByTestId("covered-delete-gb"));
    fireEvent.click(screen.getByTestId("covered-yes-gb"));
    await waitFor(() => expect(deleteCoveredAnswer).toHaveBeenCalledWith("gb"));
    await waitFor(() => expect(screen.queryByTestId("covered-gb")).toBeNull());
    expect(deleteCoveredAnswer).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("covered-in")).toBeInTheDocument();
  });

  it("is silent when there is nothing to clean up or the read fails", async () => {
    getCoveredAnswers.mockRejectedValue(new Error("403"));
    const { container } = render(<MemoryCoveredAnswers />);
    await waitFor(() => expect(getCoveredAnswers).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
