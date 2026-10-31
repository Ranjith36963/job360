import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
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
    const chipSum = count.chips.filter((k) => k !== "all").reduce((t, k) => t + count.byChip[k], 0);
    expect(chipSum).toBe(FIXTURE.n);
    expect(count.jobIds).toHaveLength(FIXTURE.n); // the rows are exactly these ids
    expect(screen.getByTestId("chip-send_yes")).toHaveTextContent("Send 2");
    expect(screen.getByTestId("chip-site_step")).toHaveTextContent("On the site 1");
  });
});
