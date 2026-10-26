import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CHANGES_EVENT } from "@/lib/live-changes";
import { keepHeld } from "@/lib/live-hold";
import { noteUserWrite } from "@/lib/live-writes";
import { NeedsYou } from "./NeedsYou";

// Needs you, live (PR 6): a job360:changes event refetches quietly; typed text survives; a card
// the user is in is never dropped until they finish.

const listAsks = vi.fn();
const answerAsk = vi.fn();
const getReadyToSend = vi.fn();
const getCvToApprove = vi.fn();
const listPeople = vi.fn();

vi.mock("@/lib/api", () => ({
  ASKS_CHANGED_EVENT: "job360:asks-changed",
  listAsks: (...a: unknown[]) => listAsks(...a),
  answerAsk: (...a: unknown[]) => answerAsk(...a),
  withdrawAsk: vi.fn(),
  confirmAsk: vi.fn(),
  getAssistantSettings: async () => ({ waiting: [] }),
  confirmSettingRequest: vi.fn(),
  declineSettingRequest: vi.fn(),
  getApplication: () => Promise.reject(new Error("no job record in this test")),
  getCvToApprove: (...a: unknown[]) => getCvToApprove(...a),
  listPeople: (...a: unknown[]) => listPeople(...a),
  getReadyToSend: (...a: unknown[]) => getReadyToSend(...a),
}));
vi.mock("./OutcomeSuggestions", () => ({
  useOutcomeSuggestions: () => ({ items: [], busy: false, act: async () => {} }), OutcomeSuggestionCard: () => null,
}));
vi.mock("./UnmatchedMail", () => ({
  useUnmatchedMail: () => ({ items: [], apps: [], resolve: async () => {} }), InboxItemCard: () => null,
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const ask = (id: number, question: string) => ({
  id, question, status: "open", application_id: null, job_title: "", job_company: "", context: "",
  asked_by: "assistant", asked_at: new Date().toISOString(), answer: null, answered_by: null, answered_at: null,
  answered_by_user: false, user_words: "", relayed_from_chat: false, said_by: "assistant", confirmed_at: null, withdrawn_at: null,
});
let openNow: unknown[] = [];

/** Let the refetch's answer land (its promises and the re-render). */
const settle = () => act(async () => {
  await new Promise((r) => setTimeout(r, 50));
});

function changed() {
  act(() => {
    window.dispatchEvent(new CustomEvent(CHANGES_EVENT, { detail: { areas: ["asks"], full: false, seq: Date.now() } }));
  });
}

beforeEach(() => {
  openNow = [];
  listAsks.mockReset().mockImplementation(async (s: string) => (s === "open" ? { asks: openNow.map((x) => ({ ...(x as object) })), open_count: openNow.length } : { asks: [], open_count: 0 }));
  // The real API client notes every save (api.ts request()); this mock does what it does.
  answerAsk.mockReset().mockImplementation(async () => {
    noteUserWrite();
    return {};
  });
  getReadyToSend.mockReset().mockResolvedValue({ paused: false, total: 0, unflagged: 0, items: [] });
  getCvToApprove.mockReset().mockResolvedValue({ items: [] });
  listPeople.mockReset().mockResolvedValue({ people: [] });
});

describe("NeedsYou live", () => {
  it("a change makes a new card appear without a reload, and the typed text survives", async () => {
    const user = userEvent.setup();
    openNow = [ask(1, "Notice period?")];
    render(<NeedsYou />);
    await user.click(await screen.findByTestId("other-open-question-1"));
    await user.type(screen.getByTestId("ask-input-1"), "Four weeks");

    // The assistant asks a second question; it sorts first.
    openNow = [ask(2, "Start date?"), ask(1, "Notice period?")];
    changed();

    expect(await screen.findByTestId("other-row-question-2")).toBeInTheDocument();
    expect(screen.getByTestId("ask-input-1")).toHaveValue("Four weeks");
    // The row the user is in kept its place (first), the new one came after it.
    const rows = screen.getAllByTestId(/^other-row-question-/).map((r) => r.getAttribute("data-testid"));
    expect(rows).toEqual(["other-row-question-1", "other-row-question-2"]);
  });

  it("a refresh that drops the card being typed in keeps it until the user finishes", async () => {
    const user = userEvent.setup();
    openNow = [ask(1, "Notice period?")];
    render(<NeedsYou />);
    await user.click(await screen.findByTestId("other-open-question-1"));
    await user.type(screen.getByTestId("ask-input-1"), "Four weeks");

    openNow = []; // answered elsewhere (another device)
    changed();
    await waitFor(() => expect(listAsks).toHaveBeenCalledTimes(4));
    await settle();
    expect(screen.getByTestId("ask-input-1")).toHaveValue("Four weeks");

    // Save only opens the confirm step: a refresh in between still keeps the card.
    await user.click(screen.getByTestId("ask-save"));
    changed();
    await waitFor(() => expect(listAsks).toHaveBeenCalledTimes(6));
    await settle();
    expect(screen.getByTestId("ask-answer-confirm")).toBeInTheDocument();
    // The save itself is "finished": the refresh after it takes the card away.
    await user.click(screen.getByTestId("ask-answer-confirm"));
    await waitFor(() => expect(screen.queryByTestId("other-row-question-1")).toBeNull());
    expect(answerAsk).toHaveBeenCalledWith(1, "Four weeks");
  });

  it("re-reads the asks and the job cards' ready, CV and people reads, with no loading flash", async () => {
    openNow = [ask(1, "Notice period?")];
    render(<NeedsYou />);
    await screen.findByTestId("other-row-question-1");
    await waitFor(() => expect(getReadyToSend).toHaveBeenCalledTimes(1));
    changed();
    expect(screen.queryByTestId("needs-you-loading")).toBeNull();
    await waitFor(() => {
      expect(listAsks).toHaveBeenCalledTimes(4);
      expect(getReadyToSend).toHaveBeenCalledTimes(2);
      expect(getCvToApprove).toHaveBeenCalledTimes(2);
      expect(listPeople).toHaveBeenCalledTimes(2);
    });
  });
});

describe("keepHeld", () => {
  const key = (x: string) => x;
  it("keeps a held item at its old place, even when the new list dropped it", () => {
    expect(keepHeld(["a", "b", "c"], ["c", "a"], key, (x) => x === "b")).toEqual(["c", "b", "a"]);
    expect(keepHeld(["a", "b"], ["x", "b", "a"], key, (x) => x === "a")).toEqual(["a", "x", "b"]);
  });
  it("changes nothing when nothing is held", () => {
    const next = ["c", "a"];
    expect(keepHeld(["a", "b"], next, key, () => false)).toBe(next);
  });
});
