import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { Ask } from "@/lib/api";
import type { JobCardData } from "@/lib/job-cards";
import { rowKind } from "@/lib/job-cards";
import { JobCard } from "./JobCard";

const answerAsk = vi.fn();
const markCvSeen = vi.fn();
const markOutreachSent = vi.fn();
const answerAskQuestions = vi.fn();
const rememberAnswers = vi.fn();
vi.mock("@/lib/api", () => ({
  answerAsk: (...a: unknown[]) => answerAsk(...a),
  markCvSeen: (...a: unknown[]) => markCvSeen(...a),
  markOutreachSent: (...a: unknown[]) => markOutreachSent(...a),
  answerAskQuestions: (...a: unknown[]) => answerAskQuestions(...a),
  rememberAnswers: (...a: unknown[]) => rememberAnswers(...a),
  downloadApplicationArtifact: vi.fn(),
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
    formFilled: true, oldestAskAt: "2026-10-20T10:00:00+00:00", ...over,
  };
}

const onChanged = vi.fn(async () => {});
beforeEach(() => {
  answerAsk.mockReset().mockResolvedValue({});
  markCvSeen.mockReset().mockResolvedValue({});
  markOutreachSent.mockReset().mockResolvedValue({});
  answerAskQuestions.mockReset().mockResolvedValue({});
  rememberAnswers.mockReset().mockResolvedValue(undefined);
  onChanged.mockClear();
});

describe("JobCard", () => {
  it("gives a blocker buttons, never a text box, and links the form", async () => {
    const captcha = ask("Solve the CAPTCHA and press Submit");
    render(<JobCard card={card([captcha])} onChanged={onChanged} />);
    expect(screen.getByText("Only you can do this")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.getByRole("link", { name: "Open the form ↗" })).toHaveAttribute("href", "https://jobs.example.com/7");
    fireEvent.click(screen.getByTestId(`did-it-${captcha.id}`));
    await waitFor(() => expect(answerAsk).toHaveBeenCalledWith(captcha.id, "I did it myself on the form."));
    expect(onChanged).toHaveBeenCalled();
  });

  it("asks a yes/no question with buttons and saves every answer and the CV in one approve", async () => {
    const visa = ask("Do you need visa sponsorship for Germany?");
    const salary = ask("Expected yearly salary (EUR)");
    render(<JobCard card={card([visa, salary], { cv: { artifactId: 5, version: 2 } })} onChanged={onChanged} />);
    const approve = screen.getByTestId("approve-all-7");
    expect(approve).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "No" }));
    fireEvent.change(screen.getByTestId(`answer-${salary.id}`), { target: { value: " €50,000 " } });
    expect(approve).toBeEnabled();
    fireEvent.click(approve);
    await waitFor(() => expect(markCvSeen).toHaveBeenCalledWith(7, 5));
    expect(answerAsk).toHaveBeenCalledWith(visa.id, "No");
    expect(answerAsk).toHaveBeenCalledWith(salary.id, "€50,000");
  });

  it("shows the full drafted message and records I sent it through the outreach door", async () => {
    const person = {
      contact: { id: 4, name: "Ada Lovelace", role: "CTO", found_via: "company_team_page" },
      message: "Hi Ada,\nI applied on 14 October.",
      linkedinUrl: "https://www.linkedin.com/in/ada",
    } as unknown as JobCardData["people"][number];
    render(<JobCard card={card([], { people: [person] })} onChanged={onChanged} />);
    expect(screen.getByText(/I applied on 14 October/)).toBeInTheDocument();
    expect(screen.getByText("Found via Company team page")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open LinkedIn profile ↗" })).toHaveAttribute("href", "https://www.linkedin.com/in/ada");
    fireEvent.click(screen.getByTestId("sent-4"));
    await waitFor(() => expect(markOutreachSent).toHaveBeenCalledWith(4, "linkedin"));
  });

  it("skips the whole job only after a confirm", async () => {
    const a = ask("Expected yearly salary (EUR)");
    const b = ask("Solve the CAPTCHA");
    render(<JobCard card={card([a, b])} onChanged={onChanged} />);
    fireEvent.click(screen.getByTestId("skip-job-7"));
    expect(answerAsk).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("skip-confirm-7"));
    await waitFor(() => expect(answerAsk).toHaveBeenCalledTimes(2));
    expect(answerAsk).toHaveBeenCalledWith(a.id, "Skip this job: do not apply.");
  });

  it("offers Finish it here on a CAPTCHA in Job360's own browser", () => {
    const captcha = { ...ask("Solve the CAPTCHA"), finish_here: true } as Ask;
    render(<JobCard card={card([captcha])} onChanged={onChanged} />);
    fireEvent.click(screen.getByTestId(`finish-here-${captcha.id}`));
    expect(screen.getByTestId("finish-browser")).toBeInTheDocument();
  });

  it("shows one row per listed question with where its answer came from, and approves them together", async () => {
    const listed = {
      ...ask("Form answers for n8n"),
      kind: "question",
      questions: [
        { key: "country", question: "Where should your contract be based?", answer: "Germany", source: "memory", remembered_on: "2026-10-12" },
        { key: "visa", question: "Do you need visa sponsorship?", answer: "", source: "none", choices: ["Yes, I need it", "No"] },
        { key: "salary", question: "Expected salary", answer: "€50,000", source: "guess" },
      ],
    } as unknown as Ask;
    render(<JobCard card={card([listed])} onChanged={onChanged} />);
    expect(screen.getByText("From memory (2026-10-12)")).toBeInTheDocument();
    expect(screen.getByText("Guessed, please check")).toBeInTheDocument();
    expect(screen.getByText("Not known yet")).toBeInTheDocument();
    const approve = screen.getByTestId("approve-all-7");
    fireEvent.click(screen.getByTestId(`q-ok-${listed.id}-country`));
    fireEvent.click(screen.getByRole("button", { name: "No" }));
    expect(approve).toBeDisabled(); // the guess is not checked yet
    fireEvent.click(screen.getByTestId(`q-edit-${listed.id}-salary`));
    fireEvent.change(screen.getByTestId(`q-input-${listed.id}-salary`), { target: { value: "€55,000" } });
    fireEvent.click(screen.getByTestId("remember-7"));
    fireEvent.click(approve);
    const answers = { country: "Germany", visa: "No", salary: "€55,000" };
    await waitFor(() => expect(answerAskQuestions).toHaveBeenCalledWith(listed.id, answers));
    expect(rememberAnswers).toHaveBeenCalledWith([
      { topic: "country", question: "Where should your contract be based?", answer: "Germany" },
      { topic: "visa", question: "Do you need visa sponsorship?", answer: "No" },
      { topic: "salary", question: "Expected salary", answer: "€55,000" },
    ]);
  });

  it("hides an old send-it-yourself reminder for someone already under People, and answers it from that row", async () => {
    const muzir = {
      contact: { id: 4, name: "Muzir Kassim", role: "CEO", found_via: "other" },
      message: "Hi Muzir, ...",
      linkedinUrl: "https://www.linkedin.com/in/muzir",
    } as unknown as JobCardData["people"][number];
    const reminder = ask("Send this LinkedIn message to Muzir Kassim (CEO, BJAK) yourself: search their name on LinkedIn");
    render(<JobCard card={card([reminder], { people: [muzir] })} onChanged={onChanged} />);
    expect(screen.queryByTestId(`reminder-${reminder.id}`)).toBeNull();
    expect(screen.getAllByText("I sent it")).toHaveLength(1); // one row, not two
    fireEvent.click(screen.getByTestId("sent-4"));
    await waitFor(() => expect(answerAsk).toHaveBeenCalledWith(reminder.id, "I sent it myself."));
    expect(markOutreachSent).toHaveBeenCalledWith(4, "linkedin");
  });

  it("answers a reminder naming two people only when both are handled", async () => {
    const person = (id: number, name: string) =>
      ({ contact: { id, name, role: "", found_via: null }, message: `Hi ${name}`, linkedinUrl: "https://www.linkedin.com/in/x" }) as unknown as JobCardData["people"][number];
    const reminder = ask("Send these LinkedIn messages yourself to German Mikulski (CTO) and Pavel Gertsberg (CEO)");
    render(<JobCard card={card([reminder], { people: [person(1, "German Mikulski"), person(2, "Pavel Gertsberg")] })} onChanged={onChanged} />);
    expect(screen.queryByTestId(`reminder-${reminder.id}`)).toBeNull();
    fireEvent.click(screen.getByTestId("sent-1"));
    await waitFor(() => expect(markOutreachSent).toHaveBeenCalledWith(1, "linkedin"));
    expect(answerAsk).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByTestId("person-1")).toBeNull()); // sent: off the card
    fireEvent.click(within(screen.getByTestId("person-2")).getByRole("button", { name: "Skip" }));
    await waitFor(() => expect(answerAsk).toHaveBeenCalledWith(reminder.id, "Skip: do not send it."));
  });

  it("renders assistant text as text, never HTML", () => {
    const { container } = render(<JobCard card={card([ask("<img src=x onerror=alert(1)>")])} onChanged={onChanged} />);
    expect(container.querySelector("img")).toBeNull();
  });
});
