/**
 * Owner-approved copy pass on /settings/connect (2026-09-27): plain three-step
 * flow, one tested claim (Claude only), personal tokens folded by default,
 * and no MCP/OAuth/bearer-token jargon in the visible copy outside the
 * developer section.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within, fireEvent } from "@testing-library/react";
import ConnectAgentPage from "./page";

const listTokens = vi.fn();
const listGrants = vi.fn();
const getProfile = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  listTokens: (...args: unknown[]) => listTokens(...args),
  listGrants: (...args: unknown[]) => listGrants(...args),
  getProfile: () => getProfile(),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });

describe("ConnectAgentPage — owner-approved copy", () => {
  beforeEach(() => {
    listTokens.mockReset().mockResolvedValue([]);
    listGrants.mockReset().mockResolvedValue([]);
    getProfile.mockReset().mockResolvedValue({ preferences: {} });
  });

  it("shows the plain heading and intro line", async () => {
    render(<ConnectAgentPage />);
    expect(
      await screen.findByRole("heading", { name: "Connect your assistant" })
    ).toBeInTheDocument();
    expect(
      screen.getByText(/your ai assistant does the work; job360 keeps the record/i)
    ).toBeInTheDocument();
  });

  it("shows the three steps in order", async () => {
    render(<ConnectAgentPage />);
    expect(await screen.findByText("Step 1 — Copy this address")).toBeInTheDocument();
    expect(screen.getByText("Step 2 — Add it in your assistant")).toBeInTheDocument();
    expect(screen.getByText("Step 3 — Say hello")).toBeInTheDocument();
  });

  it("shows Tested: works only for Claude — every other assistant carries no testing claim", async () => {
    render(<ConnectAgentPage />);
    await screen.findByText("Step 2 — Add it in your assistant");
    expect(screen.getByTestId("assistant-status-claude")).toHaveTextContent(/tested: works/i);
    for (const name of ["chatgpt", "perplexity", "grok", "gemini"]) {
      expect(screen.queryByTestId(`assistant-status-${name}`)).not.toBeInTheDocument();
    }
    // Never a guess either way for the untested four.
    expect(screen.queryByText(/we have not run a full connection/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/not tested/i)).not.toBeInTheDocument();
  });

  it("never shows the retired 'accepted this assistant's sign-in address' note", async () => {
    render(<ConnectAgentPage />);
    await screen.findByText("Step 2 — Add it in your assistant");
    expect(
      screen.queryByText(/accepted this assistant's sign-in address/i)
    ).not.toBeInTheDocument();
  });

  it("folds personal tokens under 'For developers' by default", async () => {
    render(<ConnectAgentPage />);
    await screen.findByText("Step 3 — Say hello");
    expect(
      screen.getByText(/for developers \(claude code, scripts\) — personal tokens/i)
    ).toBeInTheDocument();
    expect(screen.queryByTestId("developer-tokens-content")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("developer-tokens-toggle"));
    expect(await screen.findByTestId("developer-tokens-content")).toBeInTheDocument();
    expect(within(screen.getByTestId("developer-tokens-content")).getByLabelText("Name")).toBeInTheDocument();
  });

  it("keeps 'MCP'/'bearer token'/'OAuth' out of the visible copy outside the developer section", async () => {
    const { container } = render(<ConnectAgentPage />);
    await screen.findByText("Step 3 — Say hello");
    // The developer section is still folded shut here — its content (which
    // may legitimately show a `claude mcp add ... Bearer ...` CLI command) is
    // not in the DOM yet, so this reads only the always-visible copy.
    expect(screen.queryByTestId("developer-tokens-content")).not.toBeInTheDocument();
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/MCP client/i);
    expect(text).not.toMatch(/bearer token/i);
    expect(text).not.toMatch(/OAuth/i);
    expect(text).not.toMatch(/MCP connection/i);
  });
});
