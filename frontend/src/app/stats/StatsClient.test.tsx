import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { StatsClient, blockedText, formatRate, buildFunnel } from "./StatsClient";
import type { StatsResponse } from "@/lib/api";

const getStats = vi.fn();
vi.mock("@/lib/api", () => ({ getStats: (...a: unknown[]) => getStats(...a) }));
vi.mock("@/components/profile/LessonsList", () => ({ LessonsList: () => <div data-testid="lessons-list" /> }));

function keyed(key: string | null, label: string, over: Record<string, unknown> = {}) {
  return {
    key, label, brought: 4, applied: 3, replied: 1, interview: 1, offer: 0, rejected: 1,
    reply_rate: 0.3333, interview_rate: 0.5, offer_rate: null, ...over,
  };
}

function stats(over: Partial<StatsResponse> = {}): StatsResponse {
  return {
    applications_truncated: false,
    groups_truncated: false,
    computed_at: "2026-10-04T10:00:00Z",
    since: null,
    overall: {
      brought: 12, applied: 9, replied: 4, interview: 2, offer: 1, rejected: 3,
      reply_rate: 0.44, interview_rate: 0.22, offer_rate: 0.11,
    },
    by_country: [keyed("FR", "FR"), keyed("remote", "Remote"), keyed(null, "Not set", { reply_rate: null })],
    by_job_source: [keyed("indeed", "indeed"), keyed("pasted_by_user", "pasted_by_user")],
    by_channel: [keyed("linkedin_easy_apply", "x")],
    by_cv_version: [
      { ...keyed("v2", "v2"), profile_versions: [2] },
    ],
    by_role: [{ ...keyed("eng", ""), role: "Engineer" }],
    by_contact_found_via: [
      { key: "linkedin", label: "linkedin", contacts: 5, outreach_sent: 4, outreach_replied: 1, reply_rate: 0.25 },
      { key: null, label: "Not set", contacts: 2, outreach_sent: 0, outreach_replied: 0, reply_rate: null },
    ],
    by_contact_relationship: [
      { key: "former_colleague", label: "former_colleague", contacts: 2, outreach_sent: 2, outreach_replied: 1, reply_rate: 0.5 },
    ],
    by_outreach_variant: [
      { key: "short ask", label: "Short ask", outreach_sent: 4, outreach_replied: 1, reply_rate: 0.25, small_sample: true },
    ],
    ...over,
  } as unknown as StatsResponse;
}

describe("formatRate", () => {
  it("formats a rate as a whole percent and null as a dash", () => {
    expect(formatRate(0.3333)).toBe("33%");
    expect(formatRate(1)).toBe("100%");
    expect(formatRate(null)).toBe("—");
  });
});

describe("buildFunnel", () => {
  it("shows a dash instead of dividing by zero", () => {
    const steps = buildFunnel({ brought: 3, applied: 0, replied: 0, interview: 0, offer: 0 });
    expect(steps.map((s) => s.rate)).toEqual([null, "0% of brought in were applied to", "—", "—", "—"]);
  });

  it("leaves out a step the data does not have, and its rate", () => {
    const steps = buildFunnel({ brought: 10, applied: 5, interview: 2, offer: 1 });
    expect(steps.map((s) => s.key)).toEqual(["brought", "applied", "interview", "offer"]);
    expect(steps.find((s) => s.key === "interview")!.rate).toBeNull();
    expect(steps.find((s) => s.key === "offer")!.rate).toBe("50% of interviews led to an offer");
  });

  it("handles no data at all", () => {
    expect(buildFunnel(null)).toEqual([]);
  });
});

describe("StatsClient", () => {
  beforeEach(() => {
    getStats.mockReset();
  });

  it("shows a quiet skeleton while loading", () => {
    getStats.mockReturnValue(new Promise(() => {}));
    render(<StatsClient />);
    expect(screen.getByTestId("stats-loading")).toBeInTheDocument();
  });

  it("shows the title, the plain note and the funnel with rates in words", async () => {
    getStats.mockResolvedValue(stats());
    render(<StatsClient />);
    const funnel = await screen.findByTestId("stats-funnel");
    for (const [n, label] of [
      ["12", "Brought in"], ["9", "Applied"], ["4", "Replies"], ["2", "Interviews"], ["1", "Offers"],
    ]) {
      expect(within(funnel).getByText(label).closest("li")).toHaveTextContent(n);
    }
    expect(within(funnel).getByText("75% of brought in were applied to")).toBeInTheDocument();
    expect(within(funnel).getByText("44% of applied got a reply")).toBeInTheDocument();
    expect(within(funnel).getByText("50% of replies led to an interview")).toBeInTheDocument();
    expect(within(funnel).getByText("50% of interviews led to an offer")).toBeInTheDocument();
    expect(screen.getByText(/Job360 only counts/)).toBeInTheDocument();
  });

  it("is called Statistics, with What's working and Lessons sections", async () => {
    getStats.mockResolvedValue(stats());
    render(<StatsClient />);
    expect(await screen.findByRole("heading", { level: 1, name: "Statistics" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Your funnel" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "What's working" })).toBeInTheDocument();
    expect(screen.getByTestId("lessons-list")).toBeInTheDocument();
  });

  it("shows the score line card with counts only, and 'Not set' when no line", async () => {
    getStats.mockResolvedValue(stats({ score_line: { line: 75, above: 3, below: 2, unscored: 1 } } as never));
    const { unmount } = render(<StatsClient />);
    const card = await screen.findByTestId("stats-score-line");
    for (const t of ["Your score line", "75", "At or above", "3", "Below", "2", "No score", "1"]) {
      expect(card).toHaveTextContent(t);
    }
    unmount();
    getStats.mockResolvedValue(stats({ score_line: { line: null, above: 0, below: 0, unscored: 4 } } as never));
    render(<StatsClient />);
    expect(await screen.findByTestId("stats-score-line")).toHaveTextContent("Not set");
  });

  it("renders each section with rows, plain labels, rates, dashes and Not set", async () => {
    getStats.mockResolvedValue(stats());
    render(<StatsClient />);
    const country = await screen.findByTestId("stats-section-country");
    const rows = within(country).getAllByTestId("stats-row");
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("France");
    expect(rows[0]).toHaveTextContent("33%");
    expect(rows[0]).toHaveTextContent("50%");
    expect(rows[1]).toHaveTextContent("Remote");
    expect(rows[2]).toHaveTextContent("Not set");
    expect(rows[2]).toHaveTextContent("—");

    const source = screen.getByTestId("stats-section-source");
    expect(source).toHaveTextContent("Indeed");
    expect(source).toHaveTextContent("You pasted it");

    expect(screen.getByTestId("stats-section-channel")).toHaveTextContent("LinkedIn Easy Apply");
    expect(screen.getByTestId("stats-section-cv")).toHaveTextContent("v2");
    expect(screen.getByTestId("stats-section-role")).toHaveTextContent("Engineer");

    const people = screen.getByTestId("stats-section-people");
    expect(screen.getByText("By where you found the person")).toBeInTheDocument();
    const prow = within(people).getAllByTestId("stats-row");
    expect(prow[0]).toHaveTextContent("LinkedIn");
    expect(prow[0]).toHaveTextContent("25%");
    expect(prow[1]).toHaveTextContent("Not set");
    expect(prow[1]).toHaveTextContent("—");
  });

  it("shows By relationship with counts and reply %", async () => {
    getStats.mockResolvedValue(stats());
    render(<StatsClient />);
    const section = await screen.findByTestId("stats-section-people-relationship");
    expect(section).toHaveTextContent("By relationship");
    expect(section).toHaveTextContent("Former colleague");
    expect(section).toHaveTextContent("2");
    expect(section).toHaveTextContent("50%");
  });

  it("shows the outreach variant table", async () => {
    getStats.mockResolvedValue(stats());
    render(<StatsClient />);
    const section = await screen.findByTestId("stats-section-outreach-variant");
    expect(screen.getByText("By outreach variant")).toBeInTheDocument();
    const row = within(section).getByTestId("stats-row");
    expect(row).toHaveTextContent("Short ask");
    expect(row).toHaveTextContent("4");
    expect(row).toHaveTextContent("25%");
    expect(within(section).getByText("Sent (n)")).toBeInTheDocument();
    expect(within(section).getByText("Replied")).toBeInTheDocument();
    expect(within(section).getByText("Reply %")).toBeInTheDocument();
  });

  it("omits an empty section", async () => {
    getStats.mockResolvedValue(stats({ by_country: [], by_contact_found_via: [], by_contact_relationship: [], by_outreach_variant: [] }));
    render(<StatsClient />);
    await screen.findByTestId("stats-funnel");
    expect(screen.queryByTestId("stats-section-country")).toBeNull();
    expect(screen.queryByTestId("stats-section-people")).toBeNull();
    expect(screen.queryByTestId("stats-section-people-relationship")).toBeNull();
    expect(screen.queryByTestId("stats-section-outreach-variant")).toBeNull();
    expect(screen.getByTestId("stats-section-role")).toBeInTheDocument();
  });

  it("shows one neutral line when the load fails", async () => {
    getStats.mockRejectedValue(new Error("boom"));
    render(<StatsClient />);
    await waitFor(() =>
      expect(screen.getByTestId("stats-error")).toHaveTextContent("Couldn't load this — refresh to try again.")
    );
    expect(screen.queryByTestId("stats-funnel")).toBeNull();
  });
});

describe("StatsClient sites block", () => {
  const sites = {
    applications_without_host: 0, hosts: 1, note: "Counts per form host.", truncated: false,
    sites: [{ host: "boards.greenhouse.io", n: 4, forms_filled: 3, applied: 2, blocked: { login_wall: 2, captcha: 1 }, blocked_total: 3 }],
  };

  it("asks for the sites view and shows a facts-only table", async () => {
    getStats.mockResolvedValue(stats({ sites } as never));
    render(<StatsClient />);
    const section = await screen.findByTestId("stats-section-sites");
    expect(getStats).toHaveBeenCalledWith("sites");
    const row = within(section).getByTestId("stats-site-row");
    expect(row).toHaveTextContent("boards.greenhouse.io");
    expect(row).toHaveTextContent("Login wall 2, Captcha 1");
    for (const h of ["Site", "Applications (n)", "Forms filled", "Applied", "Blocked"]) {
      expect(within(section).getByText(h)).toBeInTheDocument();
    }
  });

  it("shows no table without a sites block", async () => {
    getStats.mockResolvedValue(stats({ sites: null } as never));
    render(<StatsClient />);
    await screen.findByTestId("stats-funnel");
    expect(screen.queryByTestId("stats-section-sites")).toBeNull();
  });

  it("blockedText lists reasons by count", () => {
    expect(blockedText({ captcha: 1, login_wall: 2, none: 0 })).toBe("Login wall 2, Captcha 1");
  });
});
