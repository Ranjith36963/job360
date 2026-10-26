import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
  usePathname: () => "/connect",
  useSearchParams: () => new URLSearchParams("tab=apps"),
}));

const grant = (id: number, month: string) => ({
  id,
  client_name: "Claude",
  created_at: `2026-${month}-01T00:00:00Z`,
  last_used_at: null,
  redirect_uri: "https://claude.ai/api/mcp/auth_callback",
  scope: "mcp",
});

const listGrants = vi.fn();
const revokeGrant = vi.fn();
vi.mock("@/lib/api", () => ({
  createToken: vi.fn(),
  getProfile: vi.fn().mockResolvedValue({ preferences: {} }),
  listGrants: (...a: unknown[]) => listGrants(...a),
  listTokens: vi.fn().mockResolvedValue([]),
  revokeGrant: (...a: unknown[]) => revokeGrant(...a),
  revokeToken: vi.fn(),
  updateProfileFields: vi.fn(),
}));
vi.mock("@/components/connect/DutyStartLinksCard", () => ({
  DutyStartLinksCard: () => <div />,
}));
vi.mock("@/components/connect/SendStartsAssistantCard", () => ({
  SendStartsAssistantCard: () => <div />,
}));

import ConnectAgentPage from "./ConnectPage";

describe("Connected apps grouping", () => {
  beforeEach(() => {
    listGrants
      .mockReset()
      .mockResolvedValue([
        grant(1, "01"),
        grant(2, "02"),
        grant(3, "03"),
        grant(4, "04"),
      ]);
    revokeGrant.mockReset().mockResolvedValue(undefined);
  });

  it("shows one row for four grants of one app, with 3 older connections", async () => {
    render(<ConnectAgentPage />);
    expect(await screen.findAllByTestId("grant-row")).toHaveLength(1);
    expect(screen.getByTestId("grant-revoke-4")).toBeInTheDocument();
    const older = screen.getByTestId("older-grants-claude");
    expect(older).toHaveTextContent("3 older connections");
    expect(screen.getAllByTestId("older-grant-row")).toHaveLength(3);
  });

  it("revoking an older grant revokes that grant's id", async () => {
    render(<ConnectAgentPage />);
    await screen.findAllByTestId("grant-row");
    fireEvent.click(screen.getByTestId("grant-revoke-2"));
    fireEvent.click(screen.getByTestId("grant-revoke-confirm-2"));
    await waitFor(() => expect(revokeGrant).toHaveBeenCalledWith(2));
  });
});
