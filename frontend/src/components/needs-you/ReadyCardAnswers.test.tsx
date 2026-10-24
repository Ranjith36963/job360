import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ReadyCard } from "./ReadyCard";
import { answer, card } from "./ready-fixtures";
import { ApiError } from "@/lib/api-error";

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

  it("memory, written and guessed rows get Verify and Edit; profile rows do not", () => {
    render(<ReadyCard card={card(64)} paused={false} onChanged={() => {}} />);
    for (const i of [0, 2, 3]) {
      expect(screen.getByTestId(`ready-answer-verify-64-${i}`)).toHaveTextContent("Verify");
      expect(screen.getByTestId(`ready-answer-edit-64-${i}`)).toHaveTextContent("Edit");
    }
    for (const i of [1]) {
      expect(screen.queryByTestId(`ready-answer-verify-64-${i}`)).toBeNull();
      expect(screen.queryByTestId(`ready-answer-edit-64-${i}`)).toBeNull();
    }
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
      answers: [answer({ source: "guessed", key: null, answer: "no", changed_to: "Yes", verified: true })],
    });
    render(<ReadyCard card={c} paused={false} onChanged={() => {}} />);
    expect(screen.getByTestId("ready-answer-changed-64-0")).toHaveTextContent("Yes");
    expect(screen.getByTestId("ready-answer-verified-64-0")).toHaveTextContent("Verified");
    expect(screen.getByTestId("ready-send-64")).toBeEnabled();
    expect(screen.getByTestId("ready-changed-64")).toHaveTextContent(
      "You changed 1 answer. Your assistant types it in before it sends.",
    );
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
