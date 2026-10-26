import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { NeedsYou } from "./NeedsYou";

const listAsks = vi.fn();
const answerAsk = vi.fn();
const withdrawAsk = vi.fn();
const confirmAsk = vi.fn();
const getAssistantSettings = vi.fn();

vi.mock("@/lib/api", () => ({
  ASKS_CHANGED_EVENT: "job360:asks-changed",
  listAsks: (...a: unknown[]) => listAsks(...a),
  answerAsk: (...a: unknown[]) => answerAsk(...a),
  withdrawAsk: (...a: unknown[]) => withdrawAsk(...a),
  confirmAsk: (...a: unknown[]) => confirmAsk(...a),
  getAssistantSettings: (...a: unknown[]) => getAssistantSettings(...a),
  confirmSettingRequest: vi.fn(),
  declineSettingRequest: vi.fn(),
  // The job cards have their own tests (JobCard.test.tsx). A job record that fails to load drops
  // its card and its asks show as plain cards under "Other questions", which these tests cover.
  getApplication: () => Promise.reject(new Error("no job record in this test")),
  getCvToApprove: async () => ({ items: [] }),
  listPeople: async () => ({ people: [] }),
  // Ready to send has its own tests (ReadyToSend.test.tsx); here only its total, for the badge.
  getReadyToSend: async () => ({ paused: false, total: readyMock.total, unflagged: 0, items: [] }),
}));
const readyMock = vi.hoisted(() => ({ total: 0 }));
// The morning check has its own tests (MorningCheck.test.tsx).
// The Batch apply panel has its own tests (BatchApplyPanel.test.tsx).
vi.mock("./BatchApplyPanel", () => ({ BatchApplyPanel: () => <aside data-testid="batch-apply-panel" /> }));
vi.mock("./MorningCheck", () => ({ MorningCheck: () => <div data-testid="morning-check" /> }));
vi.mock("./OutcomeSuggestions", () => ({ OutcomeSuggestions: () => null }));
vi.mock("./UnmatchedMail", () => ({ UnmatchedMail: () => null }));
// The live view is tested in ApplicationDecisions.test.tsx; this stand-in only offers Done.
vi.mock("@/components/applications/FinishInBrowser", () => ({
  FinishInBrowser: ({ onDone }: { onDone: () => void }) => (
    <button type="button" data-testid="finish-view" onClick={onDone}>
      Done
    </button>
  ),
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
  user_words: "",
  relayed_from_chat: false,
  said_by: "assistant",
  confirmed_at: null,
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

// An answered ask shows as one line; "Change" opens the full card these tests check.
async function openAnswered() {
  for (const b of await screen.findAllByTestId(/^answered-change-/)) fireEvent.click(b);
}

beforeEach(() => {
  listAsks.mockReset();
  answerAsk.mockReset().mockResolvedValue({});
  withdrawAsk.mockReset().mockResolvedValue({});
  getAssistantSettings.mockReset().mockResolvedValue({ waiting: [] });
});

describe("NeedsYou", () => {
  it("no longer renders the morning check or the Batch apply panel (they moved to Home, owner 2026-10-26)", async () => {
    setup([]);
    render(<NeedsYou />);
    await screen.findByTestId("needs-you-main");
    expect(screen.queryByTestId("morning-check")).toBeNull();
    expect(screen.queryByTestId("batch-apply-panel")).toBeNull();
  });

  it("a CAPTCHA in Job360's browser offers Finish it here on the card; other asks do not", async () => {
    setup([
      { ...base, id: 3, question: "Your assistant got stuck: a CAPTCHA", status: "open", finish_here: true },
      { ...base, id: 4, application_id: 8, question: "Salary?", status: "open", finish_here: false },
    ]);
    render(<NeedsYou />);
    const button = await screen.findByTestId("ask-finish-here");
    expect(screen.getAllByTestId("ask-finish-here")).toHaveLength(1);
    expect(screen.getByTestId("ask-3").contains(button)).toBe(true);
    fireEvent.click(button);
    fireEvent.click(await screen.findByTestId("finish-view"));
    expect(await screen.findByTestId("ask-finish-done")).toHaveTextContent("Did it go through?");
    expect(screen.getByRole("link", { name: /I submitted it/ })).toHaveAttribute("href", "/applications/7");
  });

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

  it("says who asked by name, never the raw author id", async () => {
    setup([{ ...openAsk, question: "Salary?", asked_by: "agent:Claude" }]);
    render(<NeedsYou />);
    expect(await screen.findByText(/asked by Claude ·/)).toBeInTheDocument();
    expect(screen.queryByText(/agent:/)).toBeNull();
  });

  it("puts a long question's tail and its context behind Details, as text", async () => {
    setup([{ ...base, id: 9, question: "Batch run paused. <b>Three</b> jobs failed; open each form.", status: "open" }]);
    const { container } = render(<NeedsYou />);
    const d = await screen.findByTestId("details-9");
    expect(d.tagName).toBe("DETAILS");
    expect(d).toHaveTextContent("<b>Three</b> jobs failed; open each form.");
    expect(d).toHaveTextContent("Form field");
    expect(screen.getByText("Batch run paused.")).toBeInTheDocument();
    expect(container.querySelector("b")).toBeNull();
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

  it("a relayed answer gets a Confirm button that records the confirmation, then reads You confirmed", async () => {
    const relayed = {
      ...base, id: 4, question: "Q4", status: "answered", answer: "Two months",
      answered_by: "agent:Claude", user_words: "two months", relayed_from_chat: true, said_by: "you",
    };
    const done = { ...relayed, confirmed_at: new Date().toISOString() };
    confirmAsk.mockResolvedValue(done);
    listAsks.mockImplementation(async (s: string) =>
      s === "open"
        ? { asks: [], open_count: 0 }
        : { asks: [confirmAsk.mock.calls.length ? done : relayed, { ...base, id: 5, question: "Q5", status: "answered", answer: "A5", answered_by: "agent:Claude" }], open_count: 0 }
    );
    render(<NeedsYou />);
    await openAnswered();
    const btn = await screen.findByTestId("confirm-relayed");
    expect(screen.getAllByTestId("confirm-relayed")).toHaveLength(1); // not on the assistant-only answer
    fireEvent.click(btn);
    await waitFor(() => expect(confirmAsk).toHaveBeenCalledWith(4));
    expect(await screen.findByTestId("ask-confirmed")).toHaveTextContent("You confirmed");
    expect(screen.queryByTestId("confirm-relayed")).toBeNull();
  });

  it("shows the user's own words when an assistant relayed them from chat", async () => {
    setup(
      [],
      [
        {
          ...base, id: 4, question: "Q4", status: "answered", answer: "Two months",
          answered_by: "agent:Claude", user_words: "its two months, give or take",
          relayed_from_chat: true, said_by: "you",
        },
        { ...base, id: 5, question: "Q5", status: "answered", answer: "A5", answered_by: "agent:Claude" },
      ]
    );
    render(<NeedsYou />);
    await openAnswered();
    const relayed = await screen.findByTestId("ask-relayed-from-chat");
    expect(relayed).toHaveTextContent("You said (via Claude): its two months, give or take");
    // only the relayed card shows it; the assistant-only answer is unchanged
    expect(screen.getAllByTestId("ask-relayed-from-chat")).toHaveLength(1);
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
    await openAnswered();
    expect(await screen.findByText("You answered on Job360")).toBeInTheDocument();
    expect(
      screen.getByText("Your assistant recorded your answer from chat")
    ).toBeInTheDocument();
    fireEvent.click(screen.getAllByTestId("ask-change")[0]);
    expect(screen.getByTestId("ask-input-2")).toHaveValue("A2");
  });

  it("puts the one job list first, then Waiting for your OK and Other questions", async () => {
    getAssistantSettings.mockResolvedValue({ waiting: [{ id: 11, key: "apply_daily_cap", value: 25, asked_by: "agent:Claude", asked_at: base.asked_at, reason: "" }] });
    setup([{ ...openAsk, application_id: null }]);
    render(<NeedsYou />);
    const other = await screen.findByTestId("other-questions");
    const jobs = screen.getByText("Waiting for you");
    const ok = await screen.findByText("Waiting for your OK");
    expect(jobs.compareDocumentPosition(ok) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(ok.compareDocumentPosition(other) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
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

  it("the badge count includes Ready to send, and never announces without it first", async () => {
    readyMock.total = 2;
    const seen: number[] = [];
    const on = (e: Event) => seen.push((e as CustomEvent).detail);
    window.addEventListener("job360:asks-changed", on);
    try {
      setup([openAsk]);
      render(<NeedsYou />);
      await waitFor(() => expect(seen).toEqual([3]));
    } finally {
      readyMock.total = 0;
      window.removeEventListener("job360:asks-changed", on);
    }
  });

  it("a changed answer goes back to the read-only view after saving", async () => {
    const done = { ...base, id: 2, question: "Q2", status: "answered", answer: "A2", answered_by_user: true };
    setup([], [done]);
    render(<NeedsYou />);
    await openAnswered();
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
