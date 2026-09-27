import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import {
  DailyCheckCard,
  dailyCheckStatusLine,
  type DailyCheckState,
} from "./page";

// ---------------------------------------------------------------------------
// Owner decision 2026-09-25 — Job360 (not any one assistant) remembers the
// user's answer to the once-only daily-check offer. The Connect page must
// show that stored answer in plain words and offer a way back to "" without
// firing any API call until the reset button is actually pressed.
// ---------------------------------------------------------------------------

describe("dailyCheckStatusLine", () => {
  it("reads '' as the assistant will still offer, once something is connected", () => {
    expect(dailyCheckStatusLine("", true)).toMatch(/will offer/i);
  });

  it("reads '' as needing a connection first, with nothing connected yet", () => {
    expect(dailyCheckStatusLine("", false)).toMatch(/connect your assistant first/i);
    // Both variants share "will offer" so a caller checking for the general
    // "not asked yet" meaning doesn't have to branch on `connected` itself.
    expect(dailyCheckStatusLine("", false)).toMatch(/will offer/i);
  });

  it("reads 'scheduled' as already set up, regardless of connection state", () => {
    expect(dailyCheckStatusLine("scheduled", true)).toMatch(/set up/i);
    expect(dailyCheckStatusLine("scheduled", false)).toMatch(/set up/i);
  });

  it("reads 'declined' as the user said no, regardless of connection state", () => {
    expect(dailyCheckStatusLine("declined", true)).toMatch(/said no/i);
    expect(dailyCheckStatusLine("declined", false)).toMatch(/said no/i);
  });

  it("says nothing for '' while the connection state is unknown (loading or failed)", () => {
    // Never tell an already-connected user to "connect first" just because
    // the tokens/apps lists haven't loaded — or failed to (reviewer-bugs, #649).
    expect(dailyCheckStatusLine("", null)).toBeNull();
    expect(dailyCheckStatusLine("declined", null)).toMatch(/said no/i);
  });
});

function renderCard(
  dailyCheck: DailyCheckState,
  onResetOffer = vi.fn(),
  resetting = false,
  connected = true
) {
  render(
    <DailyCheckCard
      dailyCheck={dailyCheck}
      connected={connected}
      onResetOffer={onResetOffer}
      resetting={resetting}
    />
  );
  return { onResetOffer };
}

describe("DailyCheckCard", () => {
  it("shows the not-asked-yet line and no reset button for '', once connected", () => {
    renderCard("");
    expect(screen.getByTestId("daily-check-status")).toHaveTextContent(
      /will offer to set this up/i
    );
    expect(screen.queryByTestId("daily-check-reset")).not.toBeInTheDocument();
  });

  it("shows a connect-first line and no reset button for '', with nothing connected", () => {
    renderCard("", vi.fn(), false, false);
    expect(screen.getByTestId("daily-check-status")).toHaveTextContent(
      /connect your assistant first/i
    );
    expect(screen.queryByTestId("daily-check-reset")).not.toBeInTheDocument();
  });

  it("shows the scheduled line and a reset button for 'scheduled'", () => {
    renderCard("scheduled");
    expect(screen.getByTestId("daily-check-status")).toHaveTextContent(
      /set up with your assistant/i
    );
    expect(screen.getByTestId("daily-check-reset")).toBeInTheDocument();
  });

  it("shows the declined line and a reset button for 'declined'", () => {
    renderCard("declined");
    expect(screen.getByTestId("daily-check-status")).toHaveTextContent(
      /you said no/i
    );
    expect(screen.getByTestId("daily-check-reset")).toBeInTheDocument();
  });

  it("does not call onResetOffer until the reset button is pressed", () => {
    const { onResetOffer } = renderCard("declined");
    expect(onResetOffer).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("daily-check-reset"));
    expect(onResetOffer).toHaveBeenCalledTimes(1);
  });

  it("disables the reset button while resetting", () => {
    renderCard("scheduled", vi.fn(), true);
    expect(screen.getByTestId("daily-check-reset")).toBeDisabled();
  });
});
