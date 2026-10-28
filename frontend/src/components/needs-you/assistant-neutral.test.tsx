import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const requestJson = vi.fn();
vi.mock("@/lib/api", () => ({
  requestJson: (...a: unknown[]) => requestJson(...a),
  getAssistantTrigger: async () => ({ set: false }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

import { PC_LOOP_COMMAND, SendStartsAssistantCard } from "@/components/connect/SendStartsAssistantCard";

const BAD = /chrome|claude/i;
const src = (p: string) => readFileSync(join(process.cwd(), "src", p), "utf-8");

/** What a user can read in a component file: code with comments, imports, test ids and identifiers removed. */
function visibleStrings(file: string): string {
  return src(file)
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "")
    .replace(/^import .*$/gm, "")
    .replace(/data-testid=(\{`[^`]*`\}|"[^"]*")/g, "")
    .replace(/testid="[^"]*"/g, "")
    .replace(/\b\w*(Chrome|OWN_CHROME)\w*\b/g, "");
}

beforeEach(() => requestJson.mockReset().mockResolvedValue({}));

describe("assistant-neutral strings (owner 2026-10-25)", () => {
  it.each([
    "components/needs-you/JobCard.tsx",
    "components/needs-you/BatchQueueCard.tsx",
    "components/needs-you/RunNowPanel.tsx",
    "components/needs-you/PersonRow.tsx",
    "lib/browser-labels.ts",
    "lib/answer-status.ts",
    "components/needs-you/AnswerStatus.tsx",
    "components/needs-you/LiveLine.tsx",
  ])("%s has no Chrome or Claude in any string", (file) => {
    expect(visibleStrings(file)).not.toMatch(BAD);
  });

  it("the SendStartsAssistantCard fallback command and its own-computer text have none", async () => {
    expect(PC_LOOP_COMMAND).not.toMatch(BAD);
    render(<SendStartsAssistantCard />);
    const details = (await screen.findByText(/No cloud\?/)).closest("details");
    expect(details?.textContent).not.toMatch(BAD);
    expect(details?.textContent).toContain("your own browser");
  });
});
