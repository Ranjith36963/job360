/**
 * A destructive button must not fire on one stray click.
 *
 * Clearing is offered precisely so profile state can be reset OFTEN (you cannot
 * trust a test you cannot reset), which means the button sits next to controls
 * the user clicks all the time. The safety property is therefore: ONE click
 * arms, a SECOND click acts, and an armed button disarms itself so it is never
 * left primed to fire on an unrelated click minutes later.
 *
 * Uses fireEvent + real timers deliberately: userEvent's own timer plumbing
 * fights vi.useFakeTimers, and the behaviour under test is plain click
 * sequencing, not typing or pointer nuance.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, act, cleanup, waitFor } from "@testing-library/react";
import { ClearButton } from "./ClearButton";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

function setup(onConfirm = vi.fn().mockResolvedValue(undefined)) {
  render(<ClearButton onConfirm={onConfirm} label="Clear CV" />);
  return { onConfirm, btn: screen.getByTestId("clear-button") };
}

describe("ClearButton", () => {
  it("does NOT clear on the first click", () => {
    const { onConfirm, btn } = setup();
    fireEvent.click(btn);
    expect(onConfirm).not.toHaveBeenCalled();
    expect(btn.getAttribute("data-armed")).toBe("true");
  });

  it("clears on the second click", async () => {
    const { onConfirm, btn } = setup();
    fireEvent.click(btn);
    fireEvent.click(btn);
    await waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(1));
  });

  it("asks before it acts, in words", () => {
    const { btn } = setup();
    expect(btn.textContent).toContain("Clear CV");
    fireEvent.click(btn);
    expect(btn.textContent).toMatch(/click again/i);
  });

  it("disarms itself so it cannot fire on a later unrelated click", () => {
    vi.useFakeTimers();
    const { onConfirm, btn } = setup();
    fireEvent.click(btn);
    expect(btn.getAttribute("data-armed")).toBe("true");
    act(() => {
      vi.advanceTimersByTime(4500);
    });
    expect(btn.getAttribute("data-armed")).toBe("false");
    fireEvent.click(btn); // a FIRST click again, not a confirm
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("cannot be double-fired while the clear is in flight", async () => {
    let release!: () => void;
    const onConfirm = vi.fn(
      () =>
        new Promise<void>((r) => {
          release = r;
        })
    );
    const { btn } = setup(onConfirm);
    fireEvent.click(btn);
    fireEvent.click(btn);
    await waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect((btn as HTMLButtonElement).disabled).toBe(true)
    );
    fireEvent.click(btn); // ignored — the button is disabled mid-flight
    expect(onConfirm).toHaveBeenCalledTimes(1);
    await act(async () => {
      release();
    });
  });
});
