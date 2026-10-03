import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { NeedsYou } from "./NeedsYou";

const listAsks = vi.fn();
const answerAsk = vi.fn();
const withdrawAsk = vi.fn();

vi.mock("@/lib/api", () => ({
  ASKS_CHANGED_EVENT: "job360:asks-changed",
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

  it("tells the header the fresh open count after every load", async () => {
    const seen: number[] = [];
    const on = (e: Event) => seen.push((e as CustomEvent).detail);
    window.addEventListener("job360:asks-changed", on);
    setup([openAsk]);
    render(<NeedsYou />);
    await waitFor(() => expect(seen).toEqual([1]));
    setup([]); // the user answered it
    fireEvent.change(screen.getByTestId("ask-input-1"), { target: { value: "Yes" } });
    fireEvent.click(screen.getByTestId("ask-save"));
    fireEvent.click(screen.getByTestId("ask-answer-confirm"));
    await waitFor(() => expect(seen).toEqual([1, 0]));
    window.removeEventListener("job360:asks-changed", on);
  });

  it("a changed answer goes back to the read-only view after saving", async () => {
    const done = { ...base, id: 2, question: "Q2", status: "answered", answer: "A2", answered_by_user: true };
    setup([], [done]);
    render(<NeedsYou />);
    fireEvent.click(await screen.findByTestId("ask-change"));
    fireEvent.change(screen.getByTestId("ask-input-2"), { target: { value: "B2" } });
    setup([], [{ ...done, answer: "B2" }]);
    fireEvent.click(screen.getByTestId("ask-save"));
    fireEvent.click(screen.getByTestId("ask-answer-confirm"));
    await waitFor(() => expect(answerAsk).toHaveBeenCalledWith(2, "B2"));
    expect(await screen.findByText("B2")).toBeInTheDocument();
    expect(screen.queryByTestId("ask-input-2")).toBeNull();
  });

  it("a failed reload after an action shows the error and a retry, keeping the list", async () => {
    setup([openAsk]);
    render(<NeedsYou />);
    await screen.findByTestId("ask-input-1");
    listAsks.mockRejectedValue(new Error("down"));
    fireEvent.click(screen.getByTestId("ask-withdraw"));
    fireEvent.click(screen.getByTestId("ask-withdraw-confirm"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load your questions.");
    expect(screen.getByTestId("ask-input-1")).toBeInTheDocument(); // old list kept
    setup([]);
    fireEvent.click(screen.getByTestId("needs-you-retry"));
    expect(await screen.findByText("Nothing needs you right now.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("pages older answers instead of stopping at the first 50", async () => {
    const page = (from: number, n: number) =>
      Array.from({ length: n }, (_, i) => ({
        ...base, id: 100 + from + i, question: `Q${from + i}`, status: "answered", answer: "a",
      }));
    listAsks.mockImplementation(async (s: string, offset = 0) =>
      s === "open"
        ? { asks: [], open_count: 0 }
        : { asks: offset === 0 ? page(0, 50) : page(50, 1), open_count: 0 }
    );
    render(<NeedsYou />);
    fireEvent.click(await screen.findByTestId("needs-you-older"));
    expect(await screen.findByText("Q50")).toBeInTheDocument();
    expect(listAsks).toHaveBeenCalledWith("answered", 50);
    expect(screen.queryByTestId("needs-you-older")).toBeNull(); // last page was short
  });
});
