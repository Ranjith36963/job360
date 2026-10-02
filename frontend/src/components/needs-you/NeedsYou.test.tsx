import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { NeedsYou } from "./NeedsYou";

const listAsks = vi.fn();
const answerAsk = vi.fn();
const withdrawAsk = vi.fn();

vi.mock("@/lib/api", () => ({
  listAsks: (...a: unknown[]) => listAsks(...a),
  answerAsk: (...a: unknown[]) => answerAsk(...a),
  withdrawAsk: (...a: unknown[]) => withdrawAsk(...a),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const base = {
  application_id: 7,
  job_title: "AI Engineer",
  job_company: "Acme",
  context: "Form field",
  asked_by: "Claude",
  asked_at: new Date().toISOString(),
  answer: null,
  answered_by: null,
  answered_at: null,
  answered_by_user: false,
  withdrawn_at: null,
};
const evil = "<img src=x onerror=alert(1)>";
const openAsk = { ...base, id: 1, question: evil, status: "open" };

function setup(open: unknown[], answered: unknown[] = []) {
  listAsks.mockImplementation(async (s: string) =>
    s === "open"
      ? { asks: open, open_count: open.length }
      : { asks: answered, open_count: open.length }
  );
}

beforeEach(() => {
  listAsks.mockReset();
  answerAsk.mockReset().mockResolvedValue({});
  withdrawAsk.mockReset().mockResolvedValue({});
});

describe("NeedsYou", () => {
  it("renders the question as literal text, never HTML", async () => {
    setup([openAsk]);
    const { container } = render(<NeedsYou />);
    expect(await screen.findByText(evil)).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByRole("link", { name: "AI Engineer · Acme" })).toHaveAttribute(
      "href",
      "/applications/7"
    );
  });

  it("answers only after the confirm", async () => {
    setup([openAsk]);
    render(<NeedsYou />);
    fireEvent.change(await screen.findByTestId("ask-input-1"), { target: { value: "Yes" } });
    fireEvent.click(screen.getByTestId("ask-save"));
    expect(screen.getByText("Save this answer? Your assistants will use it.")).toBeInTheDocument();
    expect(answerAsk).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("ask-answer-confirm"));
    await waitFor(() => expect(answerAsk).toHaveBeenCalledWith(1, "Yes"));
    await waitFor(() => expect(listAsks).toHaveBeenCalledTimes(4));
  });

  it("withdraw needs a confirm", async () => {
    setup([openAsk]);
    render(<NeedsYou />);
    fireEvent.click(await screen.findByTestId("ask-withdraw"));
    expect(withdrawAsk).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("ask-withdraw-confirm"));
    await waitFor(() => expect(withdrawAsk).toHaveBeenCalledWith(1));
  });

  it("shows provenance for both answer sources", async () => {
    setup(
      [],
      [
        { ...base, id: 2, question: "Q2", status: "answered", answer: "A2", answered_by_user: true },
        { ...base, id: 3, question: "Q3", status: "answered", answer: "A3", answered_by_user: false },
      ]
    );
    render(<NeedsYou />);
    expect(await screen.findByText("You answered on Job360")).toBeInTheDocument();
    expect(
      screen.getByText("Your assistant recorded your answer from chat")
    ).toBeInTheDocument();
    fireEvent.click(screen.getAllByTestId("ask-change")[0]);
    expect(screen.getByTestId("ask-input-2")).toHaveValue("A2");
  });

  it("shows the empty state", async () => {
    setup([]);
    render(<NeedsYou />);
    expect(await screen.findByText("Nothing needs you right now.")).toBeInTheDocument();
  });
});
