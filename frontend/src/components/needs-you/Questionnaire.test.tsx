import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

const getQuestionnaire = vi.fn();
const rememberQuestionnaire = vi.fn();
vi.mock("@/lib/api", () => ({
  getQuestionnaire: (...a: unknown[]) => getQuestionnaire(...a),
  rememberQuestionnaire: (...a: unknown[]) => rememberQuestionnaire(...a),
}));
vi.mock("@/lib/toast", () => ({ toast: { apiError: vi.fn() } }));

import { Questionnaire } from "./Questionnaire";

const row = (topic: string, question: string, answer: string | null, label: "guessed" | "not_known", from = "") => ({
  id: topic, group: "work", question, country: "GB", topic, answer, value: answer ?? "", from, state: answer ? "guessed" : "missing",
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
  rememberQuestionnaire.mockReset().mockResolvedValue({ saved: [] });
});

describe("Questionnaire (ask once, owner 2026-11-02)", () => {
  it("lists each country's questions once and remembers a row only on its OK press, for that country", async () => {
    render(<Questionnaire />);
    expect(await screen.findByTestId("questionnaire-GB")).toBeInTheDocument();
    expect(screen.queryByTestId("questionnaire-US")).toBeNull(); // nothing left for the US
    expect(screen.getByTestId("qn-from-GB-logistics.notice_period")).toHaveTextContent("Your assistant's guess");
    expect(screen.getByTestId("qn-ok-GB-contact.phone")).toBeDisabled(); // no answer yet
    fireEvent.change(screen.getByTestId("qn-input-GB-contact.phone"), { target: { value: "+44 7700 900123" } });
    expect(rememberQuestionnaire).not.toHaveBeenCalled(); // typing saves nothing
    fireEvent.click(screen.getByTestId("qn-ok-GB-contact.phone"));
    await waitFor(() => expect(rememberQuestionnaire).toHaveBeenCalledWith("GB", [
      { topic: "contact.phone", question: "Phone number", answer: "+44 7700 900123" },
    ]));
    expect(getQuestionnaire).toHaveBeenCalledTimes(2); // re-read: a remembered row leaves the card
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

  it("typing and leaving a box saves nothing; OK all sends exactly the shown text and skips empty boxes", async () => {
    render(<Questionnaire />);
    const box = await screen.findByTestId("qn-input-GB-logistics.notice_period");
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: "2 months" } });
    fireEvent.blur(box);
    await new Promise((r) => setTimeout(r, 1200)); // past any debounce
    expect(rememberQuestionnaire).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("qn-ok-all-GB"));
    await waitFor(() => expect(rememberQuestionnaire).toHaveBeenCalledTimes(1));
    expect(rememberQuestionnaire).toHaveBeenCalledWith("GB", [
      { topic: "logistics.notice_period", question: "Notice period", answer: "2 months" },
    ]);
  });

  it("OK all and remember sends every answered row of that country", async () => {
    render(<Questionnaire />);
    fireEvent.click(await screen.findByTestId("qn-ok-all-GB"));
    await waitFor(() => expect(rememberQuestionnaire).toHaveBeenCalledWith("GB", [
      { topic: "logistics.notice_period", question: "Notice period", answer: "1 month" },
    ]));
    expect(screen.getByTestId("qn-ok-all-GB")).toHaveTextContent("OK all and remember");
  });

  it("is silent when nothing is left to answer", async () => {
    getQuestionnaire.mockResolvedValue({ countries: [{ country: "GB", questions: [] }] });
    const { container } = render(<Questionnaire />);
    await waitFor(() => expect(getQuestionnaire).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
