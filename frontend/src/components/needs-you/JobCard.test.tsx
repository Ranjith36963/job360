import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { Ask } from "@/lib/api";
import type { JobCardData } from "@/lib/job-cards";
import { needsOwnBrowser, rowKind } from "@/lib/job-cards";
import { JobCard } from "./JobCard";

const answerAsk = vi.fn();
const markCvSeen = vi.fn();
const markOutreachSent = vi.fn();
const answerAskQuestions = vi.fn();
const rememberAnswers = vi.fn();
const saveAskRowAnswers = vi.fn();
vi.mock("@/lib/api", () => ({
  answerAsk: (...a: unknown[]) => answerAsk(...a),
  markCvSeen: (...a: unknown[]) => markCvSeen(...a),
  markOutreachSent: (...a: unknown[]) => markOutreachSent(...a),
  answerAskQuestions: (...a: unknown[]) => answerAskQuestions(...a),
  rememberAnswers: (...a: unknown[]) => rememberAnswers(...a),
  saveAskRowAnswers: (...a: unknown[]) => saveAskRowAnswers(...a),
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
beforeEach(() => {
  answerAsk.mockReset().mockResolvedValue({});
  markCvSeen.mockReset().mockResolvedValue({});
  markOutreachSent.mockReset().mockResolvedValue({});
  answerAskQuestions.mockReset().mockResolvedValue({});
  rememberAnswers.mockReset().mockResolvedValue(undefined);
  saveAskRowAnswers.mockReset().mockResolvedValue({});
  onChanged.mockClear();
});

describe("JobCard", () => {
  it("gives a blocker buttons, never a text box, and links the form", async () => {
    const captcha = ask("Solve the CAPTCHA and press Submit");
    render(<JobCard card={card([captcha])} onChanged={onChanged} />);
    expect(screen.getByText("Only you can do this")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
    for (const l of screen.getAllByRole("link", { name: "Open the form ↗" })) expect(l).toHaveAttribute("href", "https://jobs.example.com/7");
    expect(within(screen.getByTestId(`blocker-${captcha.id}`)).getByTestId("ask-open-empty-form")).toHaveAttribute("href", "https://jobs.example.com/7");
    fireEvent.click(screen.getByTestId(`did-it-${captcha.id}`));
    await waitFor(() => expect(answerAsk).toHaveBeenCalledWith(captcha.id, "I did it myself on the form."));
    expect(onChanged).toHaveBeenCalled();
  });

  it("keeps the application-page link on a CAPTCHA row when the card has no form URL", () => {
    const captcha = ask("Solve the CAPTCHA and press Submit");
    render(<JobCard card={card([captcha], { formUrl: "" })} onChanged={onChanged} />);
    const row = screen.getByTestId(`blocker-${captcha.id}`);
    expect(within(row).getByTestId(`row-link-${captcha.id}`)).toHaveAttribute("href", "/applications/7");
    expect(within(row).queryByTestId("ask-open-empty-form")).toBeNull();
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
    expect(screen.getByRole("link", { name: "Open profile ↗" })).toHaveAttribute("href", "https://www.linkedin.com/in/ada");
    fireEvent.click(screen.getByTestId("sent-4"));
    await waitFor(() => expect(markOutreachSent).toHaveBeenCalledWith(4, "linkedin"));
  });

  it("shows the first 3 people, then Show 2 more expands the rest", () => {
    const person = (id: number) =>
      ({ contact: { id, name: `Person ${id}`, role: "", found_via: null }, message: `Hi ${id}`, linkedinUrl: "https://www.linkedin.com/in/x" }) as unknown as JobCardData["people"][number];
    render(<JobCard card={card([], { people: [1, 2, 3, 4, 5].map(person) })} onChanged={onChanged} />);
    expect(screen.getByTestId("person-3")).toBeInTheDocument();
    expect(screen.queryByTestId("person-4")).toBeNull();
    fireEvent.click(screen.getByTestId("people-more-7"));
    expect(screen.getByTestId("person-4")).toBeInTheDocument();
    expect(screen.getByTestId("person-5")).toBeInTheDocument();
    expect(screen.queryByTestId("people-more-7")).toBeNull();
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
    expect(screen.getByTestId(`q-state-${listed.id}-country`)).toHaveTextContent("OK"); // memory
    expect(screen.getByTestId(`q-source-${listed.id}-country`).textContent).toMatch(/^From your Memory · saved /);
    expect(screen.getByTestId(`q-state-${listed.id}-visa`)).toHaveTextContent("Needs you"); // none
    expect(screen.getByTestId(`q-state-${listed.id}-salary`)).toHaveTextContent("Check");
    expect(screen.getByTestId(`q-source-${listed.id}-salary`)).toHaveTextContent("Your assistant's guess");
    expect(screen.queryByText(/Not known|Verified|Guessed/)).toBeNull();
    expect(screen.queryByTestId(`q-ok-${listed.id}-country`)).toBeNull(); // a saved fact starts OK: nothing to press
    expect(screen.queryByTestId("approve-all-7")).toBeNull(); // one primary button at a time
    fireEvent.click(screen.getByRole("button", { name: "No" }));
    await waitFor(() => expect(saveAskRowAnswers).toHaveBeenCalledWith(listed.id, { visa: "No" })); // saved at once
    expect(screen.queryByTestId("approve-all-7")).toBeNull(); // the guess is not checked yet
    await waitFor(() => expect(screen.getByTestId(`q-edit-${listed.id}-salary`)).toBeEnabled());
    fireEvent.click(screen.getByTestId(`q-edit-${listed.id}-salary`));
    fireEvent.change(screen.getByTestId(`q-input-${listed.id}-salary`), { target: { value: "€55,000" } });
    expect(screen.queryByTestId("remember-7")).toBeNull(); // no checkbox any more
    const approve = screen.getByTestId("approve-all-7");
    expect(approve).toHaveTextContent("Approve everything and continue");
    fireEvent.click(approve);
    const answers = { country: "Germany", visa: "No", salary: "€55,000" };
    await waitFor(() => expect(answerAskQuestions).toHaveBeenCalledWith(listed.id, answers));
    // Called without a checkbox; the memory answer left unchanged is not saved again.
    expect(rememberAnswers).toHaveBeenCalledWith([
      { topic: "visa", question: "Do you need visa sponsorship?", answer: "No" },
      { topic: "salary", question: "Expected salary", answer: "€55,000" },
    ]);
  });

  const listing = (over: Partial<Ask>, questions: unknown[]) => ({ ...ask("Form answers"), kind: "question", questions, ...over }) as unknown as Ask;
  const Q = (key: string, question: string, answer: string, source = "profile") => ({ key, question, answer, source });

  it("shows a question once when several open asks list it, and answers every ask", async () => {
    const a1 = listing({}, [Q("city", "City?", "Berlin"), Q("phone", "Phone?", "")]);
    const a2 = listing({}, [Q("city", "  CITY?  ", "Berlin", "guess"), Q("notice", "Notice period?", "1 month")]);
    const a3 = listing({}, [Q("city", "City?", "Berlin")]); // every key merged away
    render(<JobCard card={card([a1, a2, a3])} onChanged={onChanged} />);
    expect(screen.getAllByText("City?")).toHaveLength(1);
    expect(screen.getByText("Form answers · 3")).toBeInTheDocument();
    expect(screen.getByTestId(`q-state-${a1.id}-city`)).toHaveTextContent("OK"); // the profile copy won over the guess
    expect(screen.getByTestId(`q-source-${a1.id}-city`)).toHaveTextContent("From your profile");
    expect(screen.getByTestId(`q-pending-${a1.id}-phone`)).toBeInTheDocument();
    expect(screen.getByTestId(`q-source-${a1.id}-phone`)).toHaveTextContent("Your assistant will draft this, or write it now");
    expect(screen.getByTestId(`q-state-${a1.id}-phone`)).toHaveTextContent("Needs you");
    expect(screen.queryByTestId(`q-ok-${a1.id}-phone`)).toBeNull();
    fireEvent.click(screen.getByTestId(`q-edit-${a1.id}-phone`));
    fireEvent.change(screen.getByTestId(`q-input-${a1.id}-phone`), { target: { value: "123" } });
    fireEvent.click(screen.getByTestId("approve-all-7"));
    await waitFor(() => expect(answerAskQuestions).toHaveBeenCalledTimes(3));
    expect(answerAskQuestions).toHaveBeenCalledWith(a1.id, { city: "Berlin", phone: "123" });
    expect(answerAskQuestions).toHaveBeenCalledWith(a2.id, { city: "Berlin", notice: "1 month" });
    expect(answerAskQuestions).toHaveBeenCalledWith(a3.id, { city: "Berlin" });
  });

  it("a question the assistant could not guess reads Not known until the owner answers it", () => {
    const a = listing({}, [Q("postcode", "Postcode", "", "not_known")]);
    render(<JobCard card={card([a])} onChanged={onChanged} />);
    expect(screen.getByTestId(`q-state-${a.id}-postcode`)).toHaveTextContent("Needs you");
    expect(screen.getByTestId(`q-source-${a.id}-postcode`)).toHaveTextContent("Your assistant could not guess this");
    fireEvent.click(screen.getByTestId(`q-edit-${a.id}-postcode`));
    fireEvent.change(screen.getByTestId(`q-input-${a.id}-postcode`), { target: { value: "SW1A 1AA" } });
    expect(screen.getByTestId(`q-state-${a.id}-postcode`)).toHaveTextContent("OK");
    expect(screen.getByTestId(`q-source-${a.id}-postcode`)).toHaveTextContent("You wrote this");
  });

  it("a saved personal answer reads Verified but still waits for the owner's click", () => {
    const a = listing({}, [Q("gender", "Gender", "Female", "profile")]);
    render(<JobCard card={card([a])} onChanged={onChanged} />);
    expect(screen.getByTestId(`q-state-${a.id}-gender`)).toHaveTextContent("Needs you");
    expect(screen.getByTestId(`q-source-${a.id}-gender`)).toHaveTextContent("From your profile · only you can choose");
    expect(screen.getByTestId(`q-ok-${a.id}-gender`)).toHaveTextContent("OK");
  });

  it("Approve all pre-filled leaves consent and personal rows for the owner's own click", async () => {
    const a = listing({}, [
      Q("city", "City?", "Berlin", "guess"),
      Q("gdpr", "Do you consent to data processing?", "Yes", "guess"),
      Q("gender", "Gender", "Female", "guess"),
      Q("empty", "Anything else?", "", "none"),
    ]);
    render(<JobCard card={card([a])} onChanged={onChanged} />);
    expect(screen.getByTestId(`q-state-${a.id}-city`)).toHaveTextContent("Check");
    fireEvent.click(screen.getByTestId("approve-prefilled-7"));
    expect(screen.queryByTestId(`q-ok-${a.id}-city`)).toBeNull();
    await waitFor(() => expect(saveAskRowAnswers).toHaveBeenCalledWith(a.id, { city: "Berlin" })); // persisted, not only shown
    await waitFor(() => expect(screen.getByTestId(`q-edit-${a.id}-empty`)).toBeEnabled());
    expect(screen.getByTestId(`q-state-${a.id}-city`)).toHaveTextContent("OK");
    expect(screen.getByTestId(`q-source-${a.id}-city`)).toHaveTextContent("You confirmed this");
    expect(screen.getByTestId(`q-state-${a.id}-gdpr`)).toHaveTextContent("Needs you");
    expect(screen.getByTestId(`q-ok-${a.id}-gdpr`)).toHaveTextContent("OK");
    expect(screen.getAllByText(/only you can choose/)).toHaveLength(2);
    expect(screen.getByTestId(`q-pending-${a.id}-empty`)).toBeInTheDocument();
    expect(screen.queryByTestId(`q-ok-${a.id}-empty`)).toBeNull();
    fireEvent.click(screen.getByTestId(`q-edit-${a.id}-empty`));
    fireEvent.change(screen.getByTestId(`q-input-${a.id}-empty`), { target: { value: "No" } });
    expect(screen.queryByTestId("approve-all-7")).toBeNull();
    expect(screen.getByTestId("approve-prefilled-7")).toBeDisabled(); // nothing left it may check
    fireEvent.click(screen.getByTestId(`q-ok-${a.id}-gdpr`));
    expect(screen.getByTestId(`q-state-${a.id}-gdpr`)).toHaveTextContent("OK");
    await waitFor(() => expect(screen.getByTestId(`q-ok-${a.id}-gender`)).toBeEnabled());
    fireEvent.click(screen.getByTestId(`q-ok-${a.id}-gender`));
    await waitFor(() => expect(saveAskRowAnswers).toHaveBeenCalledWith(a.id, { gender: "Female" }));
    await waitFor(() => expect(screen.getByTestId("approve-all-7")).toBeEnabled());
    fireEvent.click(screen.getByTestId("approve-all-7"));
    await waitFor(() => expect(answerAskQuestions).toHaveBeenCalledWith(a.id, { city: "Berlin", gdpr: "Yes", gender: "Female", empty: "No" }));
    expect(rememberAnswers).toHaveBeenLastCalledWith(expect.arrayContaining([
      { topic: "empty", question: "Anything else?", answer: "No" },
    ]));
  });

  it("a personal answer the owner edits is saved at once, before Approve all (2026-10-26)", async () => {
    const a = listing({}, [Q("gender", "Gender", "Male", "guess"), Q("city", "City?", "", "none")]);
    render(<JobCard card={card([a])} onChanged={onChanged} />);
    fireEvent.click(screen.getByTestId(`q-edit-${a.id}-gender`));
    fireEvent.change(screen.getByTestId(`q-input-${a.id}-gender`), { target: { value: "Female" } });
    expect(rememberAnswers).not.toHaveBeenCalled(); // nothing saved while typing
    fireEvent.click(screen.getByTestId(`q-save-${a.id}-gender`));
    await waitFor(() => expect(rememberAnswers).toHaveBeenCalledWith([{ topic: "gender", question: "Gender", answer: "Female" }]));
    expect(screen.queryByTestId("approve-all-7")).toBeNull(); // the card itself is not done yet
  });

  it("pre-guesses consent and referral rows as Guessed, and shows an empty free-text row as waiting for the assistant", () => {
    const a = listing({}, [
      Q("privacy", "I agree to the processing of my personal data", "", "none"),
      Q("ref", "Were you referred by an employee?", "", "none"),
      Q("other", "Why this company?", "", "none"),
    ]);
    render(<JobCard card={card([a])} onChanged={onChanged} />);
    expect(within(screen.getByTestId(`q-${a.id}-privacy`)).getByText("Yes")).toBeInTheDocument();
    expect(within(screen.getByTestId(`q-${a.id}-ref`)).getByText("No")).toBeInTheDocument();
    // A consent row stays the owner's own tap, even with the pre-guess; the referral guess waits for a check.
    expect(screen.getByTestId(`q-state-${a.id}-privacy`)).toHaveTextContent("Needs you");
    expect(screen.getByTestId(`q-source-${a.id}-privacy`)).toHaveTextContent("Your assistant's guess · only you can choose");
    expect(screen.getByTestId(`q-state-${a.id}-ref`)).toHaveTextContent("Check");
    expect(screen.getByTestId(`q-source-${a.id}-ref`)).toHaveTextContent("Your assistant's guess");
    expect(screen.getByTestId(`q-pending-${a.id}-other`)).toBeInTheDocument();
    expect(screen.getByTestId(`q-state-${a.id}-other`)).toHaveTextContent("Needs you");
    expect(screen.queryByTestId(`q-input-${a.id}-other`)).toBeNull();
    fireEvent.click(screen.getByTestId(`q-edit-${a.id}-other`));
    const box = screen.getByTestId(`q-input-${a.id}-other`);
    expect(box).toHaveAttribute("placeholder", "Type your answer");
    expect(screen.getByTestId(`q-state-${a.id}-other`)).toHaveTextContent("Needs you");
    expect(screen.queryByText(/Not known|Guessed/)).toBeNull();
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

  it("names the owner (You) for a blocker with no backend label, on the row and on the card", () => {
    const captcha = ask("Solve the CAPTCHA and press Submit");
    render(<JobCard card={card([captcha])} onChanged={onChanged} />);
    expect(screen.getByTestId(`browser-chip-${captcha.id}`)).toHaveTextContent("You");
    expect(screen.getByTestId("browser-chip-card-7")).toHaveTextContent("You");
    expect(screen.queryByTestId("own-chrome-7")).toBeNull();
  });

  it("groups own-Chrome asks under their own heading, with the amber chip", () => {
    const a = ask("Solve the CAPTCHA and press Submit");
    const b = { ...ask("Please sign in on the job site"), browser: { kind: "own_browser", label: "Needs your own browser: turn your PC on", reason: "Job360 could not sign in" } } as unknown as Ask;
    render(<JobCard card={card([a, b])} onChanged={onChanged} />);
    const group = screen.getByTestId("own-chrome-7");
    expect(within(group).getByText("Needs your own browser: turn your PC on", { selector: "h4" })).toBeInTheDocument();
    expect(within(group).getByTestId(`blocker-${b.id}`)).toBeInTheDocument();
    expect(within(group).queryByTestId(`blocker-${a.id}`)).toBeNull();
    expect(screen.getByTestId(`browser-chip-${b.id}`)).toHaveTextContent("Needs your own browser: turn your PC on");
    expect(screen.getByTestId(`browser-chip-${a.id}`)).toHaveTextContent("You");
    expect(screen.getByTestId("browser-chip-card-7")).toHaveTextContent("Needs your own browser: turn your PC on");
  });

  const reach = (id: number, extra: Record<string, unknown> = {}) =>
    ({ contact: { id, name: `Person ${id}`, role: "", found_via: null, email: "", ...extra }, message: `Hi ${id}`, linkedinUrl: "https://www.linkedin.com/in/x" }) as unknown as JobCardData["people"][number];

  it("has a Job section and an Outreach section, with outreach reminders only under Outreach", () => {
    const blocker = ask("Solve the CAPTCHA and press Submit");
    const q = ask("What is your notice period?");
    const reminder = ask("Send an intro note to the hiring team yourself");
    const rem = { ...reminder, kind: "outreach" } as Ask;
    const c = card([blocker, q, rem], { people: [reach(4)], cv: { artifactId: 1, version: 2 } });
    c.asks[2] = { ask: rem, kind: "outreach" };
    render(<JobCard card={c} onChanged={onChanged} />);
    const job = screen.getByTestId("section-job-7");
    const out = screen.getByTestId("section-outreach-7");
    expect(within(job).getByRole("heading", { name: "Job" })).toBeInTheDocument();
    expect(within(out).getByRole("heading", { name: "Outreach" })).toBeInTheDocument();
    expect(within(job).getByTestId(`blocker-${blocker.id}`)).toBeInTheDocument();
    expect(within(job).getByTestId(`question-${q.id}`)).toBeInTheDocument();
    expect(within(job).getByTestId("cv-to-check")).toBeInTheDocument();
    expect(within(job).getByText("Form filled, waiting for you")).toBeInTheDocument();
    expect(within(job).getByTestId("section-form-link-7")).toHaveAttribute("href", "https://jobs.example.com/7");
    expect(within(job).queryByTestId(`reminder-${rem.id}`)).toBeNull();
    expect(within(out).getByTestId(`reminder-${rem.id}`)).toBeInTheDocument();
    expect(within(out).getByTestId("person-4")).toBeInTheDocument();
    expect(within(job).queryByTestId("person-4")).toBeNull();
  });

  it("omits the Outreach section when there is no outreach", () => {
    render(<JobCard card={card([ask("Notice period?")])} onChanged={onChanged} />);
    expect(screen.queryByTestId("section-outreach-7")).toBeNull();
  });

  it("gives every question row a link: the form, else the application page", () => {
    const q = ask("What is your notice period?");
    const { unmount } = render(<JobCard card={card([q])} onChanged={onChanged} />);
    expect(within(screen.getByTestId(`question-${q.id}`)).getByRole("link", { name: "Open the form ↗" })).toHaveAttribute("href", "https://jobs.example.com/7");
    unmount();
    render(<JobCard card={card([q], { formUrl: "" })} onChanged={onChanged} />);
    expect(within(screen.getByTestId(`question-${q.id}`)).getByRole("link", { name: "Open the application" })).toHaveAttribute("href", "/applications/7");
  });

  it("shows the label and its reason on the row and the card for own_browser and human", () => {
    const own = { ...ask("Please sign in on the job site"), browser: { kind: "own_browser", label: "Needs your own browser: turn your PC on", reason: "Sign-in needs your PC" } } as unknown as Ask;
    const human = { ...ask("Solve the CAPTCHA and press Submit"), browser: { kind: "human", label: "Only you", reason: "A person must click" } } as unknown as Ask;
    render(<JobCard card={card([own, human])} onChanged={onChanged} />);
    expect(screen.getByTestId(`browser-chip-${own.id}`)).toHaveTextContent("Needs your own browser: turn your PC on");
    expect(screen.getByTestId(`browser-reason-${own.id}`)).toHaveTextContent("Sign-in needs your PC");
    expect(screen.getByTestId(`browser-chip-${human.id}`)).toHaveTextContent("Only you");
    expect(screen.getByTestId(`browser-reason-${human.id}`)).toHaveTextContent("A person must click");
    expect(screen.getByTestId("browser-chip-card-7")).toHaveTextContent("Needs your own browser: turn your PC on");
    expect(screen.getByTestId("browser-reason-card-7")).toHaveTextContent("Sign-in needs your PC");
  });

  it("shows no reason line for job360, and ignores a bad kind", () => {
    const j = { ...ask("Solve the CAPTCHA and press Submit"), browser: { kind: "job360", label: "Job360's browser", reason: "first" } } as unknown as Ask;
    const bad = { ...ask("Please sign in"), browser: { kind: "mystery", label: "X", reason: "Y" } } as unknown as Ask;
    render(<JobCard card={card([j, bad])} onChanged={onChanged} />);
    expect(screen.queryByTestId(`browser-reason-${j.id}`)).toBeNull();
    expect(screen.getByTestId(`browser-chip-${bad.id}`)).toHaveTextContent("You"); // bad label ignored: blocker default
    expect(screen.queryByTestId(`browser-reason-${bad.id}`)).toBeNull();
  });

  it("an unlabelled question row carries no browser chip, and a backend label still beats the default", () => {
    const labelled = { ...ask("Solve the CAPTCHA and press Submit"), browser: { kind: "job360", label: "Job360's browser", reason: "" } } as unknown as Ask;
    render(<JobCard card={card([labelled])} onChanged={onChanged} />);
    expect(screen.getByTestId(`browser-chip-${labelled.id}`)).toHaveTextContent("Job360's browser");
  });

  it("puts a long ask behind Details with a one-line head", () => {
    const long = ask("Solve the CAPTCHA now. Batch run paused after three failures; please finish the check on the form.");
    render(<JobCard card={card([long])} onChanged={onChanged} />);
    const d = screen.getByTestId(`details-${long.id}`);
    expect(d.tagName).toBe("DETAILS");
    expect(d).toHaveTextContent("Batch run paused after three failures");
    expect(within(screen.getByTestId(`blocker-${long.id}`)).getByText("Solve the CAPTCHA now.")).toBeInTheDocument();
  });

  it("shows a short ask with no Details", () => {
    const short = ask("Solve the CAPTCHA and press Submit");
    render(<JobCard card={card([short])} onChanged={onChanged} />);
    expect(screen.queryByTestId(`details-${short.id}`)).toBeNull();
  });

  it("has no ask-level browser button", () => {
    const a = ask("Please sign in on the job site");
    render(<JobCard card={card([a])} onChanged={onChanged} />);
    expect(screen.queryByText("Use my assistant's browser")).toBeNull();
  });

  it("shows the pace line at the top of Outreach", () => {
    const c = card([ask("Notice period?")], { people: [reach(4)] });
    render(<JobCard card={c} onChanged={onChanged} />);
    const pace = screen.getByTestId("outreach-pace-7");
    expect(pace).toHaveTextContent("LinkedIn sends go out from your own browser only, when your PC is on. 2–3 minutes between sends, at most 10 LinkedIn sends a day.");
    expect(within(screen.getByTestId("section-outreach-7")).getByTestId("outreach-pace-7")).toBe(pace);
  });

  it("links the company and the role to the application, and shows a stored draft note as missing", () => {
    const listed = {
      ...ask("Form answers"),
      kind: "question",
      questions: [
        { key: "json", question: "Structured JSON outputs", answer: "drafted", source: "guess" },
        { key: "city", question: "City?", answer: "Berlin", source: "guess" },
      ],
    } as unknown as Ask;
    render(<JobCard card={card([listed])} onChanged={onChanged} />);
    expect(screen.getByTestId("title-link-7")).toHaveAttribute("href", "/applications/7");
    expect(screen.getByText("Acme")).toHaveAttribute("href", "/applications/7");
    expect(screen.getByTestId(`draft-missing-${listed.id}-json`)).toHaveTextContent("Draft text missing");
    expect(screen.getByText("Stored note: drafted")).toBeInTheDocument();
    expect(screen.getByTestId(`q-edit-${listed.id}-json`)).toBeInTheDocument();
    expect(screen.queryByTestId(`q-ok-${listed.id}-json`)).toBeNull();
    expect(screen.getByTestId(`q-state-${listed.id}-json`)).toHaveTextContent("Needs you");
    expect(screen.getByTestId(`q-source-${listed.id}-json`)).toHaveTextContent("Draft missing, write it or ask again");
    expect(screen.getByTestId(`q-ok-${listed.id}-city`)).toHaveTextContent("OK");
  });

  it("shows a long answer in full behind Show all", () => {
    const long = "Built and shipped agent systems in production. ".repeat(10);
    const listed = { ...ask("Form answers"), kind: "question", questions: [{ key: "x", question: "Experience", answer: long, source: "guess" }] } as unknown as Ask;
    render(<JobCard card={card([listed])} onChanged={onChanged} />);
    expect(screen.getByText("Show all")).toBeInTheDocument();
    expect(screen.getByTestId(`answer-full-${listed.id}-x`).textContent).toBe(long);
  });
});

// Owner bug 2026-10-26 (applications 113 / 127): an Edit on a "Needs your click" row was lost on reload -
// nothing saved until EVERY row was checked, and a row waiting for the assistant kept that from happening.
describe("JobCard saves each answer row on its own", () => {
  const listing = (over: Partial<Ask>, questions: unknown[]) => ({ ...ask("Form answers"), kind: "question", questions, ...over }) as unknown as Ask;
  const Q = (key: string, question: string, answer: string, source = "profile") => ({ key, question, answer, source });
  const rows = () => [
    Q("equality.ethnicity", "Use 'Indian' for ethnicity on this form?", "Indian", "memory"),
    Q("cv_ok", "CV 87 OK to use?", "", "none"), // waits for the assistant: never blocks the others
  ];

  it("an edited sensitive row saves at once, and a fresh render from the saved data shows it Verified", async () => {
    const a = listing({}, rows());
    const { unmount } = render(<JobCard card={card([a])} onChanged={onChanged} />);
    expect(screen.getByTestId(`q-state-${a.id}-equality.ethnicity`)).toHaveTextContent("Needs you");
    fireEvent.click(screen.getByTestId(`q-edit-${a.id}-equality.ethnicity`));
    fireEvent.change(screen.getByTestId(`q-input-${a.id}-equality.ethnicity`), { target: { value: " South Asian " } });
    fireEvent.click(screen.getByTestId(`q-save-${a.id}-equality.ethnicity`));
    await waitFor(() => expect(saveAskRowAnswers).toHaveBeenCalledWith(a.id, { "equality.ethnicity": "South Asian" }));
    await waitFor(() => expect(rememberAnswers).toHaveBeenCalledWith([
      { topic: "equality.ethnicity", question: "Use 'Indian' for ethnicity on this form?", answer: "South Asian" },
    ]));
    expect(answerAskQuestions).not.toHaveBeenCalled(); // the ask stays open for the waiting row
    expect(onChanged).toHaveBeenCalled();
    expect(screen.queryByTestId(`q-input-${a.id}-equality.ethnicity`)).toBeNull(); // Save closes the editor
    unmount();

    // The reload: the ask now carries the owner's saved word.
    const saved = listing({ id: a.id, answers: { "equality.ethnicity": "South Asian" } } as Partial<Ask>, rows());
    render(<JobCard card={card([saved])} onChanged={onChanged} />);
    const row = screen.getByTestId(`q-${a.id}-equality.ethnicity`);
    expect(within(row).getByText("South Asian")).toBeInTheDocument();
    expect(screen.getByTestId(`q-state-${a.id}-equality.ethnicity`)).toHaveTextContent("OK");
    expect(screen.getByTestId(`q-source-${a.id}-equality.ethnicity`)).toHaveTextContent("You changed this");
    expect(screen.queryByText(/only you can choose/)).toBeNull();
  });

  it("Looks right and a choice save their row at once; Cancel puts an edit back", async () => {
    const a = listing({}, [
      Q("gender", "Gender", "Male", "memory"),
      { ...Q("demographic_consent", "Tick the demographic-data consent checkbox?", "", "none"), choices: ["Yes", "No"] },
      Q("cv_ok", "CV 87 OK to use?", "", "none"),
    ]);
    render(<JobCard card={card([a])} onChanged={onChanged} />);
    fireEvent.click(screen.getByTestId(`q-edit-${a.id}-gender`));
    fireEvent.change(screen.getByTestId(`q-input-${a.id}-gender`), { target: { value: "Female" } });
    fireEvent.click(screen.getByTestId(`q-cancel-${a.id}-gender`));
    expect(within(screen.getByTestId(`q-${a.id}-gender`)).getByText("Male")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId(`q-ok-${a.id}-gender`));
    await waitFor(() => expect(saveAskRowAnswers).toHaveBeenCalledWith(a.id, { gender: "Male" }));
    expect(rememberAnswers).not.toHaveBeenCalled(); // the stored fact, unchanged: nothing new to remember
    fireEvent.click(await screen.findByRole("button", { name: "No" }));
    await waitFor(() => expect(saveAskRowAnswers).toHaveBeenCalledWith(a.id, { demographic_consent: "No" }));
  });

  it("a failed save puts the row back and says so", async () => {
    saveAskRowAnswers.mockRejectedValueOnce(new Error("down"));
    const a = listing({}, rows());
    render(<JobCard card={card([a])} onChanged={onChanged} />);
    fireEvent.click(screen.getByTestId(`q-ok-${a.id}-equality.ethnicity`));
    await waitFor(() => expect(screen.getByTestId(`q-ok-${a.id}-equality.ethnicity`)).toHaveTextContent("OK"));
    expect(screen.getByTestId(`q-state-${a.id}-equality.ethnicity`)).toHaveTextContent("Needs you");
  });
});
