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
  it("reads '' as the assistant will still offer", () => {
    expect(dailyCheckStatusLine("")).toMatch(/will offer/i);
  });

  it("reads 'scheduled' as already set up", () => {
    expect(dailyCheckStatusLine("scheduled")).toMatch(/set up/i);
  });

  it("reads 'declined' as the user said no", () => {
    expect(dailyCheckStatusLine("declined")).toMatch(/said no/i);
  });
});

function renderCard(
  dailyCheck: DailyCheckState,
  onResetOffer = vi.fn(),
  resetting = false
) {
  render(
    <DailyCheckCard
      dailyCheck={dailyCheck}
      onResetOffer={onResetOffer}
      resetting={resetting}
    />
  );
  return { onResetOffer };
}

describe("DailyCheckCard", () => {
  it("shows the not-asked-yet line and no reset button for ''", () => {
    renderCard("");
    expect(screen.getByTestId("daily-check-status")).toHaveTextContent(
      /will offer to set this up/i
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
