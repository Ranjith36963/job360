import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ReadyCard } from "./ReadyCard";
import { answer, card } from "./ready-fixtures";
import { ApiError } from "@/lib/api-error";
import type { SalaryFacts } from "@/lib/salary-suggest";

const confirmAnswer = vi.fn();
vi.mock("@/lib/api", () => ({
  approveSend: vi.fn(),
  declineSend: vi.fn(),
  confirmAnswer: (...a: unknown[]) => confirmAnswer(...a),
}));
const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock("sonner", () => ({ toast: { success: (m: string) => toastSuccess(m), error: (m: string) => toastError(m) } }));

beforeEach(() => {
  for (const f of [confirmAnswer, toastSuccess, toastError]) f.mockReset();
  confirmAnswer.mockResolvedValue({});
});

// card(64): 0 memory, 1 profile, 2 written, 3 guessed.
describe("Verify / Edit on each answer", () => {
  it("a web-address answer is a new-tab link, with Verify right beside it", () => {
    const linkedin = answer({ question: "LinkedIn profile", answer: "https://linkedin.com/in/ranjith-ai-engineer" });
    render(<ReadyCard card={card(64, { answers: [linkedin] })} paused={false} onChanged={() => {}} />);
    const link = screen.getByTestId("answer-link");
    expect(link.textContent).toBe("https://linkedin.com/in/ranjith-ai-engineer");
    expect(link).toHaveAttribute("href", "https://linkedin.com/in/ranjith-ai-engineer");
    expect(link).toHaveAttribute("target", "_blank");
    expect(screen.getByTestId("ready-answer-verify-64-0")).toHaveTextContent("Verify");
  });

  it("memory, written and guessed rows get Verify and Edit; profile rows get Edit only", () => {
    render(<ReadyCard card={card(64)} paused={false} onChanged={() => {}} />);
    for (const i of [0, 2, 3]) {
      expect(screen.getByTestId(`ready-answer-verify-64-${i}`)).toHaveTextContent("Verify");
      expect(screen.getByTestId(`ready-answer-edit-64-${i}`)).toHaveTextContent("Edit");
    }
    // Owner 2026-10-26: every row can be edited, whatever its source.
    expect(screen.queryByTestId("ready-answer-verify-64-1")).toBeNull();
    expect(screen.getByTestId("ready-answer-edit-64-1")).toHaveTextContent("Edit");
  });

  it("a private answer not kept says Needs your answer, never Approved, and Edit saves it", async () => {
    const salary = { question: "What is your expected salary package?", answer: "", source: "approved", key: "salary.expected", redacted: true, saved_by: null, saved_at: "2026-10-20T09:00:00+00:00" };
    const blank = { question: "Notice period?", answer: "", source: "approved", key: null, saved_by: null, saved_at: null };
    const onChanged = vi.fn();
    render(<ReadyCard card={card(122, { answers: [answer(salary), answer(blank)] })} paused={false} onChanged={onChanged} />);
    for (const i of [0, 1]) {
      const row = screen.getByTestId(`ready-answer-122-${i}`);
      expect(row).toHaveTextContent("Needs your answer");
      expect(row).not.toHaveTextContent("Approved");
      expect(screen.queryByTestId(`ready-answer-verify-122-${i}`)).toBeNull();
    }
    expect(screen.getByTestId("ready-answer-missing-122-0")).toHaveTextContent("Tap Edit to type your answer");
    fireEvent.click(screen.getByTestId("ready-answer-edit-122-0"));
    fireEvent.change(screen.getByTestId("ready-answer-input-122-0"), { target: { value: "£80,000" } });
    fireEvent.click(screen.getByTestId("ready-answer-save-122-0"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(confirmAnswer).toHaveBeenCalledWith(122, 0, 1220, "£80,000");
  });

  it("after the save the reloaded card shows the owner's value as Verified", () => {
    const saved = { question: "What is your expected salary package?", answer: "", source: "approved", redacted: true, changed_to: "£80,000", changed_reason: "owner_edit", verified: true };
    render(<ReadyCard card={card(122, { answers: [answer(saved)] })} paused={false} onChanged={() => {}} />);
    expect(screen.getByTestId("ready-answer-changed-122-0")).toHaveTextContent("£80,000");
    expect(screen.getByTestId("ready-answer-verified-122-0")).toHaveTextContent("Verified");
    expect(screen.getByTestId("ready-answer-edit-122-0")).toBeInTheDocument();
  });

  it("Verify sends the fill id and no text, then reloads the card", async () => {
    const onChanged = vi.fn();
    render(<ReadyCard card={card(64)} paused={false} onChanged={onChanged} />);
    fireEvent.click(screen.getByTestId("ready-answer-verify-64-3"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(confirmAnswer).toHaveBeenCalledWith(64, 3, 640, undefined);
    expect(toastSuccess).toHaveBeenCalledWith("Verified. Saved as your answer.");
  });

  it("Edit opens a box with the typed answer and saves the new text", async () => {
    const onChanged = vi.fn();
    render(<ReadyCard card={card(64)} paused={false} onChanged={onChanged} />);
    fireEvent.click(screen.getByTestId("ready-answer-edit-64-3"));
    const box = screen.getByTestId("ready-answer-input-64-3");
    expect(box).toHaveValue("no");
    fireEvent.change(box, { target: { value: "Yes, I hold a visa" } });
    fireEvent.click(screen.getByTestId("ready-answer-save-64-3"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(confirmAnswer).toHaveBeenCalledWith(64, 3, 640, "Yes, I hold a visa");
  });

  it("a verified answer shows the blue double tick and no Verify button, Edit stays", () => {
    const c = card(64, { answers: [answer({ verified: true, saved_by: "web" })] });
    render(<ReadyCard card={c} paused={false} onChanged={() => {}} />);
    expect(screen.getByTestId("ready-answer-verified-64-0")).toHaveTextContent("Verified");
    expect(screen.queryByTestId("ready-answer-verify-64-0")).toBeNull();
    expect(screen.getByTestId("ready-answer-edit-64-0")).toBeInTheDocument();
  });

  it("an edited answer is verified, shows the user's word, and Send stays open", () => {
    const c = card(64, {
      answers: [answer({ source: "guessed", key: null, answer: "no", changed_to: "Yes", changed_reason: "owner_edit", verified: true })],
    });
    render(<ReadyCard card={c} paused={false} onChanged={() => {}} />);
    expect(screen.getByTestId("ready-answer-changed-64-0")).toHaveTextContent("Yes");
    expect(screen.getByTestId("ready-answer-verified-64-0")).toHaveTextContent("Verified");
    expect(screen.getByTestId("ready-send-64")).toBeEnabled();
    expect(screen.getByTestId("ready-changed-64")).toHaveTextContent(
      "You changed 1 answer. Your assistant types it in before it sends.",
    );
  });

  it("saved answers the assistant will type are not the user's change (application 122)", () => {
    const c = card(64, {
      answers: [
        answer({ question: "Phone", answer: "+44 7442 564327", key: "contact.phone", saved_by: "web",
          changed_to: "+447442564327", changed_reason: "saved_answer", verified: true }),
        answer({ question: "Location and right to work", answer: "", source: "approved", key: "right_to_work.GB.yes_no",
          changed_to: "Yes", changed_reason: "saved_answer", verified: true }),
      ],
    });
    render(<ReadyCard card={c} paused={false} onChanged={() => {}} />);
    expect(screen.getByTestId("ready-changed-64")).toHaveTextContent("2 saved answers will be typed in before it sends.");
    expect(screen.getByTestId("ready-changed-64").textContent).not.toMatch(/You changed/);
    // One label and the single value that will be typed: never "(blank) -> Yes", no struck-through old value.
    const row = screen.getByTestId("ready-answer-64-1");
    expect(row).toHaveTextContent("Location and right to work");
    expect(screen.getByTestId("ready-answer-changed-64-1").textContent).toBe("Yes");
    expect(row.textContent).not.toMatch(/\(blank\)/);
    expect(row.querySelector(".line-through")).toBeNull();
    expect(screen.getByTestId("ready-answer-64-0").textContent).not.toMatch(/\+44 7442 564327/);
    expect(screen.getByTestId("ready-send-64")).toBeEnabled();
  });

  it("an edit and a saved answer on one card get one sentence each", () => {
    const c = card(64, {
      answers: [
        answer({ source: "guessed", key: null, answer: "no", changed_to: "Yes", changed_reason: "owner_edit", verified: true }),
        answer({ question: "Why Poolside?", answer: "x", source: "written", key: null,
          changed_to: "I build agents.", changed_reason: "approved_for_employer", verified: true }),
      ],
    });
    render(<ReadyCard card={c} paused={false} onChanged={() => {}} />);
    expect(screen.getByTestId("ready-changed-64")).toHaveTextContent(
      "You changed 1 answer. Your assistant types it in before it sends. 1 saved answer will be typed in before it sends.",
    );
    expect(screen.getByTestId("ready-answer-64-1")).toHaveTextContent("Your approved answer for this employer.");
  });

  it("a stale card says look again and reloads", async () => {
    const onChanged = vi.fn();
    confirmAnswer.mockRejectedValue(new ApiError(409, "changed"));
    render(<ReadyCard card={card(64)} paused={false} onChanged={onChanged} />);
    fireEvent.click(screen.getByTestId("ready-answer-verify-64-0"));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("This changed since you looked. Check it again."));
    expect(onChanged).toHaveBeenCalled();
  });
});

describe("full answers and missing drafts", () => {
  it("a long answer shows in full behind Show all", () => {
    const long = "I built agents that ship real work. ".repeat(14);
    const a = answer({ question: "Why Poolside?", answer: long, source: "written" });
    render(<ReadyCard card={card(70, { answers: [a] })} paused={false} onChanged={() => {}} />);
    expect(screen.getByText("Show all")).toBeInTheDocument();
    expect(screen.getByTestId("answer-full-70-0").textContent).toBe(long);
    expect(screen.getByTestId("ready-answer-verify-70-0")).toBeInTheDocument();
    expect(screen.getByTestId("ready-answer-edit-70-0")).toBeInTheDocument();
  });

  it("a placeholder row shows the red line, the stored note, Edit, and no Verify", () => {
    const a = answer({ question: "Structured JSON outputs", answer: "drafted", source: "guessed" });
    render(<ReadyCard card={card(71, { answers: [a] })} paused={false} onChanged={() => {}} />);
    expect(screen.getByTestId("draft-missing-71-0")).toHaveTextContent("Draft text missing: your assistant will write it on its next run");
    expect(screen.getByText("Stored note: drafted")).toBeInTheDocument();
    expect(screen.queryByTestId("ready-answer-verify-71-0")).toBeNull();
    expect(screen.getByTestId("ready-answer-edit-71-0")).toBeInTheDocument();
    expect(screen.getByTestId("ready-draft-missing-71")).toHaveTextContent("1 answer still needs");
  });

  it("the title and company link to the application", () => {
    render(<ReadyCard card={card(72)} paused={false} onChanged={() => {}} />);
    expect(screen.getByTestId("title-link-72")).toHaveAttribute("href", "/applications/72");
    expect(screen.getByTestId("company-link-72")).toHaveAttribute("href", "/applications/72");
  });
});

describe("Suggested salary from Memory", () => {
  const facts: SalaryFacts = {
    current: { amount: 45000, currency: "GBP", period: "year" },
    expected: [{ country: "GB", min: 60000, max: 80000, currency: "GBP", period: "year" }],
  };
  const priv = (question: string) => answer({ question, answer: "", source: "approved", key: null, redacted: true, saved_by: null, saved_at: null });
  const gb = (answers: unknown[]) => card(130, { job_country: "GB", answers });
  const mk = (answers: unknown[], f: SalaryFacts | undefined = facts, onChanged = () => {}) =>
    render(<ReadyCard card={gb(answers)} paused={false} onChanged={onChanged} salaryFacts={f} />);

  it("shows the suggestion on a private salary row, keeping the Needs your answer chip", () => {
    mk([priv("What is your current/last salary package?"), priv("What is your expected salary package?")]);
    expect(screen.getByTestId("ready-answer-suggest-130-0")).toHaveTextContent("Suggested from your Memory: £45,000 a year");
    expect(screen.getByTestId("ready-answer-suggest-130-1")).toHaveTextContent("Suggested from your Memory: £60,000 – £80,000 a year");
    expect(screen.getByTestId("ready-answer-130-0")).toHaveTextContent("Needs your answer");
  });

  it("Use this saves exactly that text through the same path as Edit, and only on the tap", async () => {
    const onChanged = vi.fn();
    mk([priv("What is your current/last salary package?")], facts, onChanged);
    expect(confirmAnswer).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("ready-answer-use-130-0"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(confirmAnswer).toHaveBeenCalledWith(130, 0, 1300, "£45,000 a year");
  });

  it("Edit opens with the suggestion in the box", () => {
    mk([priv("What is your expected salary package?")]);
    fireEvent.click(screen.getByTestId("ready-answer-edit-130-0"));
    expect(screen.getByTestId("ready-answer-input-130-0")).toHaveValue("£60,000 – £80,000 a year");
  });

  it("shows no suggestion when the profile has no salary, or no facts were loaded", () => {
    mk([priv("What is your expected salary package?")], { current: null, expected: [] });
    expect(screen.queryByTestId("ready-answer-suggest-130-0")).toBeNull();
    expect(screen.queryByTestId("ready-answer-use-130-0")).toBeNull();
    expect(screen.getByTestId("ready-answer-130-0")).toHaveTextContent("Needs your answer");
  });

  it("shows no suggestion on a blank row that is not about salary", () => {
    mk([answer({ question: "Notice period?", answer: "", source: "approved", key: null, saved_by: null, saved_at: null })]);
    expect(screen.queryByTestId("ready-answer-suggest-130-0")).toBeNull();
    fireEvent.click(screen.getByTestId("ready-answer-edit-130-0"));
    expect(screen.getByTestId("ready-answer-input-130-0")).toHaveValue("");
  });
});
