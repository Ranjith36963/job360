import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import type { AssistantSettingsView } from "@/lib/api";
import { OutreachLimitRows } from "./OutreachLimitRows";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
const f = (effective: number) => ({ value: null, effective });
const view = {
  outreach_per_company: f(5), network_per_hour: f(5), linkedin_sends_per_day: f(20), email_sends_per_day: f(40),
} as unknown as AssistantSettingsView;

describe("OutreachLimitRows", () => {
  it("shows each effective value under its plain label, with max, and the LinkedIn note", () => {
    render(<OutreachLimitRows view={view} busy={false} save={vi.fn()} />);
    const li = screen.getByLabelText("LinkedIn sends per day") as HTMLInputElement;
    expect(li.value).toBe("20");
    expect(li.max).toBe("100");
    expect((screen.getByLabelText("People per company you applied to") as HTMLInputElement).max).toBe("10");
    expect((screen.getByLabelText("New network people per hour") as HTMLInputElement).max).toBe("20");
    expect((screen.getByLabelText("Emails per day") as HTMLInputElement).value).toBe("40");
    expect(screen.getByText(/about 100 a week is a common limit/)).toBeTruthy();
  });
  it("saves a changed number on Enter, refuses one above the max", () => {
    const save = vi.fn();
    render(<OutreachLimitRows view={view} busy={false} save={save} />);
    const box = screen.getByLabelText("Emails per day");
    fireEvent.change(box, { target: { value: "60" } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(save).toHaveBeenCalledWith("assistant_settings.email_sends_per_day", 60);
    const li = screen.getByLabelText("LinkedIn sends per day");
    fireEvent.change(li, { target: { value: "500" } });
    fireEvent.blur(li);
    expect(save).toHaveBeenCalledTimes(1);
  });
});
