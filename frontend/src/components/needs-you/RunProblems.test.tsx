import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

const getRunProblems = vi.fn();
const getDutyTriggers = vi.fn();
vi.mock("@/lib/api", () => ({
  getRunProblems: (...a: unknown[]) => getRunProblems(...a),
  getDutyTriggers: (...a: unknown[]) => getDutyTriggers(...a),
}));

import { RunProblems } from "./RunProblems";

describe("RunProblems", () => {
  it("shows one plain card per failing job with what to do", async () => {
    getDutyTriggers.mockResolvedValue({ duties: [] });
    getRunProblems.mockResolvedValue({ problems: [{
      duty: "hunt", label: "Hunt", failures_in_row: 3, reason: "sign_in_needed",
      title: "Hunt failed 3 times in a row: a connected app needs you to sign in again.",
      what_to_do: "Open your assistant's connector settings, sign in again, then press Run now.",
    }] });
    render(<RunProblems />);
    const card = await screen.findByTestId("run-problem-hunt");
    expect(card).toHaveTextContent("Hunt failed 3 times in a row: a connected app needs you to sign in again.");
    expect(card).toHaveTextContent("sign in again, then press Run now.");
  });

  it("stays silent when nothing failed or the list cannot load", async () => {
    getDutyTriggers.mockRejectedValue(new Error("403"));
    getRunProblems.mockRejectedValue(new Error("500"));
    const { container } = render(<RunProblems />);
    await new Promise((r) => setTimeout(r, 0));
    expect(container).toBeEmptyDOMElement();
  });
});
