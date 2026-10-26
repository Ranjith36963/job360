import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { FinishInBrowser } from "./FinishInBrowser";
import { LIVE_SUBMITTED_EVENT } from "@/lib/live-submitted";

const browserLive = vi.fn();
const success = vi.fn();
vi.mock("@/lib/api", () => ({ browserLive: (...a: unknown[]) => browserLive(...a) }));
vi.mock("@/lib/toast", () => ({ toast: { error: vi.fn(), apiError: vi.fn(), success: (m: string) => success(m) } }));

const SHOT = { status: "ok", url: "https://ats.rippling.com/acme/jobs/1", title: "Apply", width: 1280, height: 900, image: "data:image/jpeg;base64,AA" };

beforeEach(() => {
  browserLive.mockReset();
  success.mockReset();
});

describe("FinishInBrowser", () => {
  it("submitted: says Applied, closes the view and tells the page to re-read", async () => {
    browserLive.mockImplementation(async (s: { action: string }) =>
      s.action === "click" ? { ...SHOT, submitted: true } : SHOT,
    );
    const onDone = vi.fn();
    const heard = vi.fn();
    window.addEventListener(LIVE_SUBMITTED_EVENT, heard);
    render(<FinishInBrowser applicationId={138} onDone={onDone} />);
    const picture = await screen.findByRole("button", { name: /Click where you would click/ });
    fireEvent.click(picture, { detail: 1, clientX: 10, clientY: 10 });
    expect(await screen.findByTestId("live-submitted")).toHaveTextContent("Applied — Job360 saw the confirmation page.");
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(success).toHaveBeenCalledWith("Applied — Job360 saw the confirmation page.");
    expect((heard.mock.calls[0][0] as CustomEvent).detail).toBe(138);
    window.removeEventListener(LIVE_SUBMITTED_EVENT, heard);
  });

  it("a click with no confirmation keeps the live view", async () => {
    browserLive.mockResolvedValue(SHOT);
    const onDone = vi.fn();
    render(<FinishInBrowser applicationId={138} onDone={onDone} />);
    const picture = await screen.findByRole("button", { name: /Click where you would click/ });
    fireEvent.click(picture, { detail: 1, clientX: 10, clientY: 10 });
    await waitFor(() => expect(browserLive).toHaveBeenCalledTimes(2));
    expect(screen.queryByTestId("live-submitted")).toBeNull();
    expect(screen.getByTestId("finish-view")).toBeInTheDocument();
    expect(onDone).not.toHaveBeenCalled();
  });
});
