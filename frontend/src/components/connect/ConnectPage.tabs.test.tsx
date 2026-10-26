import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

let tabParam: string | null = null;
const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  usePathname: () => "/connect",
  useSearchParams: () => new URLSearchParams(tabParam ? `tab=${tabParam}` : ""),
}));
vi.mock("@/components/settings/CalendarFeedSection", () => ({ CalendarFeedSection: () => null }));
vi.mock("@/components/profile/AssistantTab", () => ({ AssistantTab: () => <p data-testid="assistant-tab-stub" /> }));
vi.mock("@/components/connect/JobSitesPanel", () => ({ JobSitesPanel: () => <p data-testid="job-sites-stub" /> }));
vi.mock("@/lib/api", () => ({
  createToken: vi.fn(),
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

describe("ConnectAgentPage tabs", () => {
  beforeEach(() => {
    tabParam = null;
    replace.mockClear();
  });

  it("renders the four tabs and defaults to Setup", () => {
    render(<ConnectAgentPage />);
    for (const k of ["setup", "schedules", "apps", "developers"]) {
      expect(screen.getByTestId(`connect-tab-${k}`)).toBeInTheDocument();
    }
    expect(screen.getByTestId("connect-tab-setup")).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByText(/Connected apps$/i, { selector: "h3, [data-slot=card-title]" })).toBeNull();
  });

  it("?tab=apps shows Connected apps", async () => {
    tabParam = "apps";
    render(<ConnectAgentPage />);
    expect(screen.getByTestId("connect-tab-apps")).toHaveAttribute("aria-selected", "true");
    expect(await screen.findAllByText(/connected apps/i)).not.toHaveLength(0);
    expect(screen.queryByTestId("send-card")).toBeNull();
  });

  it("has the six tabs in the owner's order", () => {
    render(<ConnectAgentPage />);
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "Setup",
      "Assistant configure",
      "Schedules & start links",
      "Job sites",
      "Connected apps",
      "Developers",
    ]);
  });

  it("?tab=assistant and ?tab=job-sites open those tabs", () => {
    tabParam = "assistant";
    const a = render(<ConnectAgentPage />);
    expect(screen.getByTestId("assistant-tab-stub")).toBeInTheDocument();
    a.unmount();
    tabParam = "job-sites";
    render(<ConnectAgentPage />);
    expect(screen.getByTestId("job-sites-stub")).toBeInTheDocument();
  });

  it("clicking a tab updates the URL", () => {
    render(<ConnectAgentPage />);
    fireEvent.click(screen.getByTestId("connect-tab-schedules"));
    expect(replace).toHaveBeenCalledWith("/connect?tab=schedules", { scroll: false });
    expect(screen.getByTestId("send-card")).toBeInTheDocument();
  });
});
