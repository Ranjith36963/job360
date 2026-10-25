import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

const getDutyTriggers = vi.fn();
vi.mock("@/lib/api", () => ({ getDutyTriggers: (...a: unknown[]) => getDutyTriggers(...a) }));

import { StartLinkProblems, startFailed } from "./StartLinkProblems";

const row = (duty: string, label: string, last_result: string) => ({
  duty, label, set: true, host: "api.anthropic.com", kind: "claude_routine", has_token: true,
  updated_at: "2026-10-25T10:00:00Z", last_fired_at: "2026-10-25T10:01:00Z", last_result,
});

describe("StartLinkProblems", () => {
  it("names each duty whose last start failed, and nothing else", async () => {
    getDutyTriggers.mockResolvedValue({ duties: [
      row("hunt", "Hunt", "failed: 401"), row("batch", "Apply", "started"),
      row("inbox", "Gmail check", "rate_limited"), { duty: "reach", label: "People hunt", set: false },
    ] });
    render(<StartLinkProblems />);
    expect(await screen.findByTestId("start-failed-hunt")).toHaveTextContent("Hunt: the start link did not work.");
    expect(screen.getByTestId("start-failed-inbox")).toHaveTextContent("busy, waiting as it asked");
    expect(screen.queryByTestId("start-failed-batch")).toBeNull();
  });

  it("stays silent when every start worked or the list cannot load", async () => {
    expect(startFailed(row("hunt", "Hunt", "refused: refused") as never)).toBe(true);
    expect(startFailed(row("hunt", "Hunt", "started") as never)).toBe(false);
    getDutyTriggers.mockRejectedValue(new Error("403"));
    const { container } = render(<StartLinkProblems />);
    await new Promise((r) => setTimeout(r, 0));
    expect(container).toBeEmptyDOMElement();
  });
});
