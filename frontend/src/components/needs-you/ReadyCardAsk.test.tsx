import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Ask } from "@/lib/api";
import { ReadyCard } from "./ReadyCard";
import { card } from "./ready-fixtures";

const approveSend = vi.fn();
const answerAskQuestions = vi.fn();
const rememberAnswers = vi.fn();
const saveAskRowAnswers = vi.fn();
vi.mock("@/lib/api", () => ({
  approveSend: (...a: unknown[]) => approveSend(...a),
  declineSend: vi.fn(),
  confirmAnswer: vi.fn(),
  answerAskQuestions: (...a: unknown[]) => answerAskQuestions(...a),
  rememberAnswers: (...a: unknown[]) => rememberAnswers(...a),
  saveAskRowAnswers: (...a: unknown[]) => saveAskRowAnswers(...a),
}));
vi.mock("@/lib/assistant-start", () => ({ startAssistantAfterSend: vi.fn() }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const q = (key: string, question: string, answer: string, choices: string[] | null = null) =>
  ({ key, question, answer, source: answer ? "profile" : "none", choices }) as NonNullable<Ask["questions"]>[number];

const ask166 = {
  id: 166, application_id: 64, status: "open", withdrawn_at: null, kind: "question", question: "Answer these", context: "",
  questions: [
    q("salary", "Expected salary", "90000"),
    q("sms", "May we text you about this application (SMS consent)?", "", ["Yes", "No", "Leave blank"]),
  ],
} as unknown as Ask;

beforeEach(() => {
  for (const f of [approveSend, answerAskQuestions, rememberAnswers, saveAskRowAnswers]) f.mockReset().mockResolvedValue({});
});

describe("Send with an open ask", () => {
  it("shows the ask's questions inline and keeps Send disabled until every row has an answer", async () => {
    render(<ReadyCard card={card(64)} asks={[ask166]} paused={false} onChanged={() => {}} />);
    expect(screen.getByTestId("ready-ask-questions")).toBeInTheDocument();
    const send = screen.getByTestId("ready-send-64");
    expect(send).toBeDisabled();
    // A saved profile fact is already OK: no OK button to press.
    expect(screen.getByTestId("q-state-166-salary")).toHaveTextContent("OK");
    expect(screen.queryByTestId("q-ok-166-salary")).toBeNull();
    expect(screen.getByTestId("q-state-166-sms")).toHaveTextContent("Needs you");
    expect(send).toBeDisabled(); // the SMS row is still open
    fireEvent.click(screen.getByRole("button", { name: "Leave blank" }));
    await waitFor(() => expect(send).not.toBeDisabled());
    fireEvent.click(send);
    await waitFor(() => expect(approveSend).toHaveBeenCalled());
    expect(answerAskQuestions).toHaveBeenCalledWith(166, { salary: "90000", sms: "Leave blank" });
    expect(answerAskQuestions.mock.invocationCallOrder[0]).toBeLessThan(approveSend.mock.invocationCallOrder[0]);
  });

  it("no open ask with questions: no section, Send is enabled", () => {
    render(<ReadyCard card={card(64)} asks={[{ ...ask166, status: "answered" } as Ask]} paused={false} onChanged={() => {}} />);
    expect(screen.queryByTestId("ready-ask-questions")).toBeNull();
    expect(screen.getByTestId("ready-send-64")).not.toBeDisabled();
  });
});
