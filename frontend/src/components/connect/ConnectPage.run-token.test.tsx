import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

let tabParam: string | null = null;
const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  usePathname: () => "/connect",
  useSearchParams: () => new URLSearchParams(tabParam ? `tab=${tabParam}` : ""),
}));
vi.mock("@/components/settings/CalendarFeedSection", () => ({ CalendarFeedSection: () => null }));
vi.mock("@/components/profile/AssistantTab", () => ({
  AssistantTab: ({ part }: { part: string }) => <p data-testid="assistant-tab-stub" data-part={part} />,
}));
vi.mock("@/components/connect/JobSitesPanel", () => ({ JobSitesPanel: () => <p data-testid="job-sites-stub" /> }));
vi.mock("@/lib/api", () => ({
  createToken: vi.fn().mockResolvedValue({
    id: 1, name: "x", prefix: "j360_ab", created_at: "2026-11-03", last_used_at: null,
    for_scheduled_runs: false, token: "j360_abc",
  }),
  getProfile: vi.fn().mockResolvedValue({ preferences: {} }),
  listGrants: vi.fn().mockResolvedValue([]),
  listTokens: vi.fn().mockResolvedValue([]),
  revokeGrant: vi.fn(),
  revokeToken: vi.fn(),
  updateProfileFields: vi.fn(),
}));
vi.mock("@/components/connect/DutyStartLinksCard", () => ({
  DutyStartLinksCard: () => <div data-testid="duty-card" />,
}));
vi.mock("@/components/connect/SendStartsAssistantCard", () => ({
  SendStartsAssistantCard: () => <div data-testid="send-card" />,
}));

import ConnectAgentPage from "./ConnectPage";

import { createToken } from "@/lib/api";

// Owner 2026-11-02 (option b): the scheduled tasks get their own token; one checkbox marks it.
describe("Create a token: for scheduled runs", () => {
  beforeEach(() => {
    tabParam = "developers";
    vi.mocked(createToken).mockClear();
  });

  async function mint(name: string, tick: boolean) {
    render(<ConnectAgentPage />);
    fireEvent.click(screen.getByTestId("developer-tokens-toggle"));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: name } });
    if (tick) fireEvent.click(screen.getByRole("checkbox", { name: /for scheduled runs only/i }));
    fireEvent.click(screen.getByRole("button", { name: "Create token" }));
    await waitFor(() => expect(createToken).toHaveBeenCalledTimes(1));
  }

  it("a normal token is not a run token", async () => {
    await mint("laptop Claude Code", false);
    expect(createToken).toHaveBeenCalledWith("laptop Claude Code", false);
  });

  it("the ticked box sends for_scheduled_runs", async () => {
    await mint("scheduled runs", true);
    expect(createToken).toHaveBeenCalledWith("scheduled runs", true);
  });
});
