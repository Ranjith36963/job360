import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import type { ApplicationControls, Ask } from "@/lib/api";
import type { JobCardData } from "@/lib/job-cards";
import { cardShowsFormHandoff, needsOwnBrowser, rowKind } from "@/lib/job-cards";
import { JobCard } from "@/components/needs-you/JobCard";
import { ApplicationDecisions } from "./ApplicationDecisions";

vi.mock("@/lib/api", () => ({
  answerAsk: vi.fn(), answerAskQuestions: vi.fn(), downloadApplicationArtifact: vi.fn(), markCvSeen: vi.fn(),
  markOutreachSent: vi.fn(), rememberAnswers: vi.fn(), saveAskRowAnswers: vi.fn(), updateContact: vi.fn(),
  approveSend: vi.fn(), clearDuplicate: vi.fn(), declineSend: vi.fn(), resolveBlocked: vi.fn(),
  setAutofill: vi.fn(), submittedBlocked: vi.fn(), refillForm: vi.fn(), browserLive: vi.fn(),
}));
vi.mock("@/lib/toast", () => ({ toast: { success: vi.fn(), apiError: vi.fn(), error: vi.fn(), info: vi.fn() } }));

const captcha = {
  id: 5, application_id: 7, job_title: "AI Engineer", job_company: "Acme", question: "Solve the CAPTCHA",
  context: "", asked_by: "Claude", asked_at: "2026-10-20T10:00:00+00:00", answer: null, answered_by: null,
  answered_at: null, answered_by_user: false, withdrawn_at: null, status: "open", kind: "captcha",
  finish_here: true, held_seconds: 1500, can_refill: true,
} as unknown as Ask;

const card: JobCardData = {
  applicationId: 7, company: "Acme", role: "AI Engineer", formUrl: "https://jobs.example.com/7",
  asks: [{ ask: captcha, kind: rowKind(captcha) }], people: [], cv: null, latestCv: null, formFilled: true,
  oldestAskAt: "2026-10-20T10:00:00+00:00", ownBrowserCount: needsOwnBrowser(captcha) ? 1 : 0,
  ownBrowser: needsOwnBrowser(captcha),
};

const controls = {
  application_id: 7, cv: null, declined: null, autofill: { mode: "unset", by: null, where: null, at: null },
  duplicate: { same_job: null, same_company_30d: 0, flag: "", cleared: null },
  blocked: {
    reason: "captcha", reason_label: "a CAPTCHA", step: "Job360 browser", page_host: "jobs.example.com",
    detail: "", by: "token:claude-code", at: "2026-10-08T10:00:00Z", ask_id: 5,
  },
} as unknown as ApplicationControls;

describe("the application page's CAPTCHA hand-off", () => {
  it("shows ONE FormHandoff (the Needs-you card's) and keeps the blocked section's own controls", () => {
    expect(cardShowsFormHandoff(card)).toBe(true);
    render(
      <>
        <ul><JobCard card={card} onChanged={async () => undefined} showHeader={false} /></ul>
        <ApplicationDecisions applicationId={7} controls={controls} onChanged={() => undefined}
          handoffShownAbove={cardShowsFormHandoff(card)}
          handoff={{ finishHere: true, canRefill: true, heldSeconds: 1500 }} />
      </>,
    );
    expect(screen.getAllByTestId("form-handoff")).toHaveLength(1);
    expect(within(screen.getByTestId(`blocker-${captcha.id}`)).getByTestId(`finish-here-${captcha.id}`)).toBeTruthy();
    expect(screen.queryByTestId("blocked-finish-here")).toBeNull();
    const blocked = screen.getByTestId("blocked-state");
    expect(within(blocked).getByTestId("blocked-i-submitted")).toBeTruthy();
    expect(within(blocked).getByTestId("blocked-resolve-button")).toBeTruthy();
  });

  it("without a Needs-you card, the blocked section still offers its own hand-off", () => {
    render(<ApplicationDecisions applicationId={7} controls={controls} onChanged={() => undefined}
      handoff={{ finishHere: true, canRefill: true, heldSeconds: 1500 }} />);
    expect(screen.getAllByTestId("form-handoff")).toHaveLength(1);
    expect(screen.getByTestId("blocked-finish-here")).toBeTruthy();
  });
});
