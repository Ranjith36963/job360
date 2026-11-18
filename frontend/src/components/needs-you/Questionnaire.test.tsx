import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";

const getQuestionnaire = vi.fn();
const rememberQuestionnaire = vi.fn();
vi.mock("@/lib/api", () => ({
  getQuestionnaire: (...a: unknown[]) => getQuestionnaire(...a),
  rememberQuestionnaire: (...a: unknown[]) => rememberQuestionnaire(...a),
}));
vi.mock("@/lib/toast", () => ({ toast: { apiError: vi.fn() } }));

import { Questionnaire } from "./Questionnaire";

const row = (topic: string, question: string, answer: string | null, label: "guessed" | "not_known", from = "", country = "GB") => ({
  id: topic, group: "work", question, country, topic, answer, value: answer ?? "", from, state: answer ? "guessed" : "missing",
  verified: false, label, seen: 3, per_job: false, sensitive: false,
});
const data = {
  countries: [
    { country: "GB", questions: [row("logistics.notice_period", "Notice period", "1 month", "guessed", "your assistant's guess"),
      row("contact.phone", "Phone number", null, "not_known")] },
    { country: "US", questions: [] },
  ],
};

beforeEach(() => {
  getQuestionnaire.mockReset().mockResolvedValue(data);
  rememberQuestionnaire.mockReset().mockImplementation(async (_cc: string, items: { topic: string }[]) =>
    ({ saved: items.map((i) => i.topic), not_saved: {} }));
});
afterEach(() => vi.useRealTimers());

/** Let the card's one-second save timer run. */
const afterTyping = () => act(async () => {
  await new Promise((r) => setTimeout(r, 1100));
});

describe("Questionnaire (answer once; autosave, owner 2026-11-18)", () => {
  it("a typed answer saves itself a second after typing stops, for that place, and says Saved", async () => {
    render(<Questionnaire />);
    expect(await screen.findByTestId("questionnaire-GB")).toBeInTheDocument();
    expect(screen.queryByTestId("questionnaire-US")).toBeNull(); // nothing left for the US
    expect(screen.queryByText(/OK all and remember/)).toBeNull(); // no per-row OK, no OK all
    expect(screen.queryByRole("button", { name: /^OK$/ })).toBeNull();
    fireEvent.change(screen.getByTestId("qn-input-GB-contact.phone"), { target: { value: "+44 7700 900123" } });
    expect(rememberQuestionnaire).not.toHaveBeenCalled(); // not on every key
    await afterTyping();
    await waitFor(() => expect(rememberQuestionnaire).toHaveBeenCalledWith("GB", [
      { topic: "contact.phone", question: "Phone number", answer: "+44 7700 900123" },
    ]));
    expect(await screen.findByTestId("qn-from-GB-contact.phone")).toHaveTextContent("Saved ✓");
    expect(rememberQuestionnaire).toHaveBeenCalledTimes(1);
  });

  it("leaving the box saves at once; an empty box saves nothing", async () => {
    render(<Questionnaire />);
    const box = await screen.findByTestId("qn-input-GB-contact.phone");
    fireEvent.change(box, { target: { value: "" } });
    fireEvent.blur(box);
    await afterTyping();
    expect(rememberQuestionnaire).not.toHaveBeenCalled();
    fireEvent.change(box, { target: { value: "+44 1" } });
    fireEvent.blur(box);
    await waitFor(() => expect(rememberQuestionnaire).toHaveBeenCalledTimes(1));
    await afterTyping();
    expect(rememberQuestionnaire).toHaveBeenCalledTimes(1); // the blur's save, not a second one
  });

  it("an answer the server cannot keep says why and how, in plain words, never silently (owner bug 2026-11-18)", async () => {
    getQuestionnaire.mockResolvedValue({ countries: [{ country: "NORTH_AMERICA", questions: [
      row("salary.NORTH_AMERICA", "Expected salary in North America", null, "not_known", "", "NORTH_AMERICA")] }] });
    rememberQuestionnaire.mockResolvedValue({ saved: [], not_saved: { "salary.NORTH_AMERICA": "a Memory fact (...)" } });
    render(<Questionnaire />);
    expect(await screen.findByTestId("questionnaire-name-NORTH_AMERICA")).toHaveTextContent("North America");
    expect(screen.queryByText("NORTH_AMERICA")).toBeNull();
    fireEvent.change(screen.getByTestId("qn-input-NORTH_AMERICA-salary.NORTH_AMERICA"), { target: { value: "70k to 120k" } });
    await afterTyping();
    await waitFor(() => expect(rememberQuestionnaire).toHaveBeenCalledWith("NORTH_AMERICA", [
      { topic: "salary.NORTH_AMERICA", question: "Expected salary in North America", answer: "70k to 120k" },
    ]));
    expect(await screen.findByTestId("qn-from-NORTH_AMERICA-salary.NORTH_AMERICA"))
      .toHaveTextContent("Not saved yet: add a currency, e.g. 70000-120000 USD a year");
  });

  it("a work-status row offers its four values as one tap each, and a tap saves at once", async () => {
    getQuestionnaire.mockResolvedValue({ countries: [{ country: "GB", questions: [
      row("right_to_work.GB.work_authorization", "Are you legally allowed to work in GB?", null, "not_known")] }] });
    render(<Questionnaire />);
    fireEvent.click(await screen.findByRole("button", { name: "Need sponsorship" }));
    await waitFor(() => expect(rememberQuestionnaire).toHaveBeenCalledWith("GB", [
      { topic: "right_to_work.GB.work_authorization", question: "Are you legally allowed to work in GB?", answer: "needs sponsorship" },
    ]));
    expect(screen.getByTestId("qn-input-GB-right_to_work.GB.work_authorization")).toHaveValue("needs sponsorship");
  });

  it("the one button confirms the assistant's untouched guesses only", async () => {
    render(<Questionnaire />);
    const button = await screen.findByTestId("qn-ok-all-GB");
    expect(button).toHaveTextContent("This guess is right");
    fireEvent.click(button);
    await waitFor(() => expect(rememberQuestionnaire).toHaveBeenCalledWith("GB", [
      { topic: "logistics.notice_period", question: "Notice period", answer: "1 month" },
    ]));
    await waitFor(() => expect(screen.queryByTestId("qn-ok-all-GB")).toBeNull()); // nothing left to confirm
  });

  it("pre-fills the box from value, shows the from line, the no-guess line, and has no Edit or Not known", async () => {
    getQuestionnaire.mockResolvedValue({ countries: [{ country: "GB", questions: [
      { ...row("contact.website", "Website", null, "guessed", "from your portfolio link"), value: "https://me.dev" },
      row("contact.phone", "Phone number", null, "not_known"),
    ] }] });
    render(<Questionnaire />);
    expect(await screen.findByTestId("qn-input-GB-contact.website")).toHaveValue("https://me.dev");
    expect(screen.getByTestId("qn-from-GB-contact.website")).toHaveTextContent("From your portfolio link");
    expect(screen.getByTestId("qn-input-GB-contact.phone")).toHaveValue("");
    expect(screen.getByTestId("qn-from-GB-contact.phone")).toHaveTextContent("Your assistant hasn't guessed this yet");
    expect(screen.queryByText(/not known/i)).toBeNull();
    expect(screen.queryByRole("button", { name: /edit/i })).toBeNull();
  });

  it("is silent when nothing is left to answer", async () => {
    getQuestionnaire.mockResolvedValue({ countries: [{ country: "GB", questions: [] }] });
    const { container } = render(<Questionnaire />);
    await waitFor(() => expect(getQuestionnaire).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("a conflict row asks which is right and starts empty; Use picks one and saves it", async () => {
    getQuestionnaire.mockResolvedValue({ countries: [{ country: "GB", questions: [
      { ...row("sponsorship.GB.yes_no", "Do you need sponsorship?", null, "guessed"), conflict: ["Yes", "No"] },
      { ...row("a.b", "Three-way", null, "guessed"), conflict: ["A", "B", "C"] },
    ] }] });
    render(<Questionnaire />);
    expect(await screen.findByTestId("qn-conflict-GB-sponsorship.GB.yes_no"))
      .toHaveTextContent("You saved both Yes and No for this question in GB — which is right?");
    expect(screen.getByTestId("qn-conflict-GB-a.b")).toHaveTextContent("You saved A, B and C for this question in GB");
    const box = screen.getByTestId("qn-input-GB-sponsorship.GB.yes_no");
    expect(box).toHaveValue("");
    expect(screen.queryByTestId("qn-ok-all-GB")).toBeNull(); // a conflict is never a guess to confirm
    fireEvent.click(screen.getByRole("button", { name: "Use No" }));
    expect(box).toHaveValue("No");
    await waitFor(() => expect(rememberQuestionnaire).toHaveBeenCalledWith("GB", [
      { topic: "sponsorship.GB.yes_no", question: "Do you need sponsorship?", answer: "No" },
    ]));
  });
});
