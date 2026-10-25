import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

let tabParam: string | null = null;
const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  usePathname: () => "/settings/connect",
  useSearchParams: () => new URLSearchParams(tabParam ? `tab=${tabParam}` : ""),
}));
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

import ConnectAgentPage from "./page";

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

  it("clicking a tab updates the URL", () => {
    render(<ConnectAgentPage />);
    fireEvent.click(screen.getByTestId("connect-tab-schedules"));
    expect(replace).toHaveBeenCalledWith("/settings/connect?tab=schedules", { scroll: false });
    expect(screen.getByTestId("send-card")).toBeInTheDocument();
  });
});
