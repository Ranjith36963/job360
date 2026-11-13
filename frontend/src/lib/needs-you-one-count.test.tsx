import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { NeedsYouBadge } from "@/components/layout/useOpenAsks";
import { NeedsYouControls } from "@/components/needs-you/NeedsYouControls";
import { fromServerCount } from "./needs-you-items";
import { waitsSentence } from "./home";

vi.mock("@/lib/api", () => ({ listAsks: vi.fn() }));

// ONE response (GET /api/needs-you-count): the nav badge, the dashboard headline and the Needs-you summary and
// chips all show the same number, because none of them counts anything itself.
const FIXTURE = {
  n: 6, jobs: 6, other: 0, jobs_waiting_on_you: 6,
  by_actor: { you: 6, your_pc: 2, assistant: 1, job360: 0, employer: 3 },
  by_kind: { send_yes: 2, approve_cv: 1, answers: 2, site_step: 1, queued: 2, assistant_step: 1, waiting_employer: 3 },
  job_ids: [1, 2, 3, 4, 5, 6],
  job_ids_by_kind: { send_yes: [1, 2], approve_cv: [3], answers: [4, 5], site_step: [6] },
  // The fixed filters: job 6 has a CAPTCHA AND a form to check, so it is in both; All counts it once.
  filters: [
    ["all", "All", 6, [1, 2, 3, 4, 5, 6], "todo"], ["signin_captcha", "Sign in / CAPTCHA", 1, [6], "todo"],
    ["check_send", "Check & send", 3, [1, 2, 6], "todo"], ["answer_once", "Answer once", 2, [4, 5], "todo"],
    ["cv_weak", "CV still weak", 1, [3], "todo"], ["finish_by_hand", "Finish by hand", 0, [], "todo"],
    ["went_out", "Did it go out?", 0, [], "todo"], ["strong_cvs", "Strong CVs (AI reviewed)", 0, [], "view"],
    ["posting_closed", "Posting closed", 0, [], "stat"], ["other", "Other", 0, [], "todo"],
  ].map(([key, label, count, job_ids, kind]) => ({ key, label, count, job_ids, kind })) as never,
};

describe("one count", () => {
  it("the badge, the headline, the summary and the chips agree on the same fixture", () => {
    const count = fromServerCount(FIXTURE);
    render(
      <>
        <NeedsYouBadge count={FIXTURE.n} />
        <NeedsYouControls count={count} settled show="all" onShow={() => {}} sort="urgent" onSort={() => {}} />
      </>,
    );
    expect(screen.getByTestId("needs-you-badge")).toHaveTextContent("6");
    expect(waitsSentence(FIXTURE.by_actor)).toBe("6 jobs wait on you · 2 on your PC · 1 on your assistant");
    expect(screen.getByTestId("needs-you-summary")).toHaveTextContent("6 things need you");
    expect(screen.getByTestId("chip-all")).toHaveTextContent("All 6");
    expect(count.jobIds).toHaveLength(FIXTURE.n); // the rows are exactly these ids
    expect(screen.getByTestId("chip-check_send")).toHaveTextContent("Check & send 3");
    expect(screen.getByTestId("chip-signin_captcha")).toHaveTextContent("Sign in / CAPTCHA 1");
    expect(count.idsByKind.check_send).toContain(6); // a job shows under every filter it has a need in
    expect(count.idsByKind.signin_captcha).toContain(6);
  });

  it("all ten chips are rendered in the fixed order, at zero too, even when nothing needs you", () => {
    const empty = fromServerCount({ ...FIXTURE, n: 0, jobs: 0, job_ids: [], job_ids_by_kind: {}, filters: [] });
    render(<NeedsYouControls count={empty} settled show="all" onShow={() => {}} sort="urgent" onSort={() => {}} />);
    const ids = screen.getAllByTestId(/^chip-/).map((el) => el.getAttribute("data-testid"));
    expect(ids).toEqual([
      "chip-all", "chip-signin_captcha", "chip-check_send", "chip-answer_once", "chip-cv_weak", "chip-finish_by_hand",
      "chip-went_out", "chip-strong_cvs", "chip-posting_closed", "chip-other"]);
    expect(screen.getByTestId("chip-finish_by_hand")).toHaveTextContent("Finish by hand 0");
    expect(screen.getByTestId("chip-posting_closed")).toHaveTextContent("Posting closed 0");
  });

  it("selecting a chip reports its key, also one at zero", () => {
    const onShow = vi.fn();
    render(<NeedsYouControls count={fromServerCount(FIXTURE)} settled show="all" onShow={onShow} sort="urgent" onSort={() => {}} />);
    fireEvent.click(screen.getByTestId("chip-went_out"));
    expect(onShow).toHaveBeenCalledWith("went_out");
  });
});
