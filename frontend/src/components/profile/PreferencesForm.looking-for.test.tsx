import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act, within } from "@testing-library/react";
import { PreferencesForm } from "./PreferencesForm";

describe("PreferencesForm - what you're looking for", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("empty on a new account: nothing is invented (rule #29)", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PreferencesForm preferences={{}} onSave={onSave} loading={false} />);
    fireEvent.change(screen.getByPlaceholderText(/Experienced data scientist/i), { target: { value: "x" } });
    await act(async () => {
      vi.advanceTimersByTime(900);
    });
    const sent = onSave.mock.calls[0][0];
    expect(sent.experience_years_min).toBeNull();
    expect(sent.experience_years_max).toBeNull();
    expect(sent.looking_for).toEqual([]);
    expect(sent.dealbreakers).toEqual([]);
    expect(sent.avoid_companies).toEqual([]);
  });

  it("loads stored values and autosaves edits", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <PreferencesForm
        preferences={{ experience_years_min: 3, experience_years_max: 6, dealbreakers: ["on-call"] }}
        onSave={onSave}
        loading={false}
      />,
    );
    expect(screen.getByText("What you're looking for")).toBeInTheDocument();
    expect((screen.getByTestId("years-min") as HTMLInputElement).value).toBe("3");
    expect((screen.getByTestId("years-max") as HTMLInputElement).value).toBe("6");
    expect(within(screen.getByTestId("dealbreakers-list")).getByText("on-call")).toBeInTheDocument();
    fireEvent.change(screen.getByTestId("years-max"), { target: { value: "8" } });
    const box = within(screen.getByTestId("avoid-companies-list")).getByRole("textbox");
    fireEvent.change(box, { target: { value: "Acme" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await act(async () => {
      vi.advanceTimersByTime(900);
    });
    const sent = onSave.mock.calls.at(-1)![0];
    expect(sent.experience_years_min).toBe(3);
    expect(sent.experience_years_max).toBe(8);
    expect(sent.avoid_companies).toEqual(["Acme"]);
    expect(sent.dealbreakers).toEqual(["on-call"]);
  });
});
