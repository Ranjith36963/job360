import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Ask } from "@/lib/api";
import type { JobCardData } from "@/lib/job-cards";
import { needsOwnBrowser, rowKind } from "@/lib/job-cards";
import { JobCard } from "./JobCard";

const answerAsk = vi.fn();
const markCvSeen = vi.fn();
const markOutreachSent = vi.fn();
const markOutreachSkipped = vi.fn();
const answerAskQuestions = vi.fn();
const rememberAskAnswers = vi.fn();
const saveAskRowAnswers = vi.fn();
const recordApplicationReceipt = vi.fn();
const resolveSubmit = vi.fn();
vi.mock("@/lib/api", () => ({
  answerAsk: (...a: unknown[]) => answerAsk(...a),
  markCvSeen: (...a: unknown[]) => markCvSeen(...a),
  markOutreachSent: (...a: unknown[]) => markOutreachSent(...a),
  markOutreachSkipped: (...a: unknown[]) => markOutreachSkipped(...a),
  answerAskQuestions: (...a: unknown[]) => answerAskQuestions(...a),
  rememberAskAnswers: (...a: unknown[]) => rememberAskAnswers(...a),
  saveAskRowAnswers: (...a: unknown[]) => saveAskRowAnswers(...a),
  recordApplicationReceipt: (...a: unknown[]) => recordApplicationReceipt(...a),
  resolveSubmit: (...a: unknown[]) => resolveSubmit(...a),
  downloadApplicationArtifact: vi.fn(),
  updateContact: vi.fn(),
}));
vi.mock("@/components/applications/FinishInBrowser", () => ({ FinishInBrowser: () => <div data-testid="finish-browser" /> }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

let nextId = 1;
function ask(question: string): Ask {
  return {
    id: nextId++, application_id: 7, job_title: "AI Engineer", job_company: "Acme", question, context: "",
    asked_by: "Claude", asked_at: "2026-10-20T10:00:00+00:00", answer: null, answered_by: null, answered_at: null,
    answered_by_user: false, withdrawn_at: null, status: "open",
  } as Ask;
}

function card(asks: Ask[], over: Partial<JobCardData> = {}): JobCardData {
  return {
    applicationId: 7, company: "Acme", role: "AI Engineer", formUrl: "https://jobs.example.com/7",
    asks: asks.map((a) => ({ ask: a, kind: rowKind(a) })), people: [], cv: null, latestCv: null,
    formFilled: true, oldestAskAt: "2026-10-20T10:00:00+00:00",
    ownBrowserCount: asks.filter(needsOwnBrowser).length, ownBrowser: asks.some(needsOwnBrowser), ...over,
  };
}

const onChanged = vi.fn(async () => {});

const Q = "The site says you already applied. Mark it applied?";

describe("already-applied row", () => {
  beforeEach(() => {
    recordApplicationReceipt.mockReset().mockResolvedValue({});
    answerAsk.mockReset().mockResolvedValue({});
    onChanged.mockClear();
  });

  it("is its own kind and shows the question with two buttons and no text box", () => {
    const a = ask(Q);
    expect(rowKind(a)).toBe("already_applied");
    render(<JobCard card={card([a])} onChanged={onChanged} />);
    expect(screen.getByText(Q)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Mark applied" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "I didn't apply" })).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByTestId("form-answers")).toBeNull();
  });

  it("Mark applied opens the confirm box with open items, then records the receipt", async () => {
    const a = ask(Q);
    render(<JobCard card={card([a], { openItems: ["upload your CV"] })} onChanged={onChanged} />);
    fireEvent.click(screen.getByRole("button", { name: "Mark applied" }));
    expect(recordApplicationReceipt).not.toHaveBeenCalled();
    expect(screen.getByTestId("mark-applied-open-items")).toHaveTextContent("upload your CV");
    fireEvent.click(screen.getByRole("button", { name: "Yes, record it" }));
    await waitFor(() => expect(recordApplicationReceipt).toHaveBeenCalledWith(7, {}));
    expect(answerAsk).not.toHaveBeenCalled();
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("I didn't apply answers no and marks nothing applied", async () => {
    const a = ask(Q);
    render(<JobCard card={card([a])} onChanged={onChanged} />);
    fireEvent.click(screen.getByRole("button", { name: "I didn't apply" }));
    await waitFor(() => expect(answerAsk).toHaveBeenCalledWith(a.id, "no"));
    expect(recordApplicationReceipt).not.toHaveBeenCalled();
  });
});
