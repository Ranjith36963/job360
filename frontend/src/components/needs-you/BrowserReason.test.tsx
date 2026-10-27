import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { BrowserReason } from "./BrowserReason";
import { REASON_WORDS, reasonWords } from "@/lib/browser-labels";
import type { BrowserLabel } from "@/lib/browser-contract";

// The server's real route reasons (backend routing.ROUTE_REASONS). None may ever show raw.
const CODES = ["job360_login_required", "job360_bot_check", "job360_safety_stop", "password", "mfa", "only_you", "link_unclear"];

const label = (kind: BrowserLabel["kind"], reason: string): BrowserLabel => ({ kind, label: "chip", reason });

describe("BrowserReason shows the server's reason code in plain words", () => {
  it.each(CODES)("%s reads as a sentence, never the code", (code) => {
    render(<BrowserReason label={label(code.startsWith("job360") ? "own_browser" : "human", code)} testid="why" />);
    const line = screen.getByTestId("why");
    expect(line.textContent).toBe(REASON_WORDS[code]);
    expect(line.textContent).not.toBe(code);
    expect(line.textContent).not.toMatch(/_/);
  });

  it("link_unclear renders its plain line for an own-browser chip", () => {
    render(<BrowserReason label={label("own_browser", "link_unclear")} testid="why" />);
    expect(screen.getByTestId("why")).toHaveTextContent("The apply link has no clear web address, so your own browser checks it.");
  });

  it("only_you says the step is the user's", () => {
    render(<BrowserReason label={label("human", "only_you")} testid="why" />);
    expect(screen.getByTestId("why")).toHaveTextContent("This step is yours: no browser does it for you.");
  });

  it("shows no line for Job360's browser, the LinkedIn choice, or an unknown code", () => {
    for (const l of [label("job360", "default"), label("own_browser", "linkedin_own_browser"), label("human", "some_new_code")]) {
      const { container, unmount } = render(<BrowserReason label={l} testid="why" />);
      expect(container.textContent).toBe("");
      unmount();
    }
  });

  it("keeps an older server's written sentence as it is", () => {
    expect(reasonWords("Job360's browser hit a sign-in page")).toBe("Job360's browser hit a sign-in page");
  });
});
