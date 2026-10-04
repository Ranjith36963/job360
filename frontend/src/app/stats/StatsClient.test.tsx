import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { StatsClient, formatRate } from "./StatsClient";
import type { StatsResponse } from "@/lib/api";

const getStats = vi.fn();
vi.mock("@/lib/api", () => ({ getStats: (...a: unknown[]) => getStats(...a) }));

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

describe("StatsClient", () => {
  beforeEach(() => {
    getStats.mockReset();
  });

  it("shows a quiet skeleton while loading", () => {
    getStats.mockReturnValue(new Promise(() => {}));
    render(<StatsClient />);
    expect(screen.getByTestId("stats-loading")).toBeInTheDocument();
  });

  it("shows the title, the plain note and the overall numbers", async () => {
    getStats.mockResolvedValue(stats());
    render(<StatsClient />);
    const overall = await screen.findByTestId("stats-overall");
    for (const [n, label] of [
      ["12", "brought"], ["9", "applied"], ["4", "replied"], ["2", "interviews"], ["1", "offers"], ["3", "rejected"],
    ]) {
      const item = within(overall).getByText(label).closest("li")!;
      expect(item).toHaveTextContent(n);
    }
    expect(screen.getByText(/Job360 only counts/)).toBeInTheDocument();
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

  it("omits an empty section", async () => {
    getStats.mockResolvedValue(stats({ by_country: [], by_contact_found_via: [] }));
    render(<StatsClient />);
    await screen.findByTestId("stats-overall");
    expect(screen.queryByTestId("stats-section-country")).toBeNull();
    expect(screen.queryByTestId("stats-section-people")).toBeNull();
    expect(screen.getByTestId("stats-section-role")).toBeInTheDocument();
  });

  it("shows one neutral line when the load fails", async () => {
    getStats.mockRejectedValue(new Error("boom"));
    render(<StatsClient />);
    await waitFor(() =>
      expect(screen.getByTestId("stats-error")).toHaveTextContent("Couldn't load this — refresh to try again.")
    );
    expect(screen.queryByTestId("stats-overall")).toBeNull();
  });
});
