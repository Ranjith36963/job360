import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("posthog-js", () => ({ default: { capture: vi.fn() } }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { AssistantStepsCard, ASSISTANT_STEPS } from "../ConnectSteps";

const NAMES = ["Claude", "ChatGPT", "Gemini", "Perplexity", "Grok"];

describe("AssistantStepsCard", () => {
  it("renders one card per assistant, in the same structure", () => {
    render(<AssistantStepsCard />);
    expect(ASSISTANT_STEPS.map((a) => a.name)).toEqual(NAMES);
    for (const name of NAMES) {
      const key = name.toLowerCase();
      const card = screen.getByTestId(`assistant-card-${key}`);
      expect(within(card).getByText(name)).toBeInTheDocument();
      expect(within(card).getByText("What you need")).toBeInTheDocument();
      expect(within(card).getByTestId(`assistant-status-${key}`)).toBeInTheDocument();
      expect(within(card).getAllByRole("listitem").length).toBeGreaterThan(1);
      expect(within(card).getByTestId(`assistant-address-${key}`)).toHaveValue(
        `${window.location.origin}/api/mcp`
      );
      expect(within(card).getByRole("button", { name: /copy address/i })).toBeInTheDocument();
    }
  });

  it("says Tested only for Claude", () => {
    render(<AssistantStepsCard />);
    for (const name of NAMES) {
      const badge = screen.getByTestId(`assistant-status-${name.toLowerCase()}`);
      expect(badge).toHaveTextContent(name === "Claude" ? /^Tested$/ : /^Not tested yet$/);
    }
  });

  it("names Claude only inside Claude's own card", () => {
    render(<AssistantStepsCard />);
    for (const name of NAMES.filter((n) => n !== "Claude")) {
      expect(screen.getByTestId(`assistant-card-${name.toLowerCase()}`)).not.toHaveTextContent(/claude/i);
    }
  });
});
