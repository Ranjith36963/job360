import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import ConnectAgentPage from "../page";

const listTokens = vi.fn();
const listGrants = vi.fn();

vi.mock("@/lib/api", () => ({
  createToken: vi.fn(),
  listGrants: (...args: unknown[]) => listGrants(...args),
  listTokens: (...args: unknown[]) => listTokens(...args),
  revokeGrant: vi.fn(),
  revokeToken: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

// jsdom has no real clipboard by default.
Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });

describe("ConnectAgentPage — daily check (owner decision, 2026-09-25)", () => {
  beforeEach(() => {
    listTokens.mockReset().mockResolvedValue([]);
    listGrants.mockReset().mockResolvedValue([]);
  });

  it("renders the daily-check prompt naming record_event, list_applications and due=true", async () => {
    render(<ConnectAgentPage />);
    await screen.findByText(/daily check \(scheduled task\)/i);

    const prompt = screen.getByTestId("daily-check-prompt") as HTMLTextAreaElement;
    expect(prompt.value).toContain("record_event");
    expect(prompt.value).toContain("list_applications");
    expect(prompt.value).toContain("due=true");
    expect(prompt.value).toContain("quiet_days=7");
    expect(prompt.value).toContain("follow_up_on");
  });

  it("has a copy button for the prompt", async () => {
    render(<ConnectAgentPage />);
    await screen.findByText(/daily check \(scheduled task\)/i);
    expect(screen.getByRole("button", { name: /copy prompt/i })).toBeInTheDocument();
  });
});
