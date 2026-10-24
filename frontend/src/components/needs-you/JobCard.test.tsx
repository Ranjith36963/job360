import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Ask } from "@/lib/api";
import type { JobCardData } from "@/lib/job-cards";
import { rowKind } from "@/lib/job-cards";
import { JobCard } from "./JobCard";

const answerAsk = vi.fn();
const markCvSeen = vi.fn();
const markOutreachSent = vi.fn();
vi.mock("@/lib/api", () => ({
  answerAsk: (...a: unknown[]) => answerAsk(...a),
  markCvSeen: (...a: unknown[]) => markCvSeen(...a),
  markOutreachSent: (...a: unknown[]) => markOutreachSent(...a),
  downloadApplicationArtifact: vi.fn(),
}));
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

  it("renders assistant text as text, never HTML", () => {
    const { container } = render(<JobCard card={card([ask("<img src=x onerror=alert(1)>")])} onChanged={onChanged} />);
    expect(container.querySelector("img")).toBeNull();
  });
});
