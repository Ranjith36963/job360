import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createEvent, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { FinishInBrowser } from "./FinishInBrowser";

const browserLive = vi.fn();
vi.mock("@/lib/api", () => ({ browserLive: (...a: unknown[]) => browserLive(...a) }));
vi.mock("@/lib/toast", () => ({ toast: { error: vi.fn(), apiError: vi.fn() } }));

// The real page is 1280x800; the picture is shown at 320x200 (a quarter size, as on a phone).
const SHOT = { status: "ok", image: "data:image/jpeg;base64,AA==", width: 1280, height: 800, title: "Apply" };

beforeEach(() => {
  browserLive.mockReset();
  browserLive.mockResolvedValue(SHOT);
});
afterEach(() => {
  document.body.style.overflow = "";
});

async function open(props: { startMaximized?: boolean } = {}) {
  const onDone = vi.fn();
  render(<FinishInBrowser applicationId={7} onDone={onDone} {...props} />);
  const pic = await screen.findByRole("button", { name: /Job360's browser showing Apply/ });
  vi.spyOn(pic, "getBoundingClientRect").mockReturnValue({
    left: 10, top: 100, width: 320, height: 200, right: 330, bottom: 300, x: 10, y: 100, toJSON: () => ({}),
  });
  browserLive.mockClear();
  return { pic, onDone };
}

function pointer(type: "pointerDown" | "pointerUp", el: Element, x: number, y: number) {
  // jsdom has no PointerEvent: build the event and give it the position.
  const ev = createEvent[type](el);
  Object.defineProperty(ev, "clientX", { value: x });
  Object.defineProperty(ev, "clientY", { value: y });
  fireEvent(el, ev);
}

describe("FinishInBrowser", () => {
  it("opens as a card and toggles Maximize / Minimize", async () => {
    await open();
    expect(screen.getByTestId("finish-view")).toBeInTheDocument();
    expect(screen.queryByTestId("live-fullscreen")).toBeNull();
    fireEvent.click(screen.getByTestId("live-maximize"));
    const full = screen.getByTestId("live-fullscreen");
    expect(full).toHaveAttribute("aria-modal", "true");
    expect(document.body.style.overflow).toBe("hidden");
    // every control stays in the full-screen bar
    for (const name of ["Enter", "Tab", "Scroll up", "Scroll down", "Done", "Back", "Send"]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
    expect(screen.getByLabelText("Type into the box you clicked")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("live-minimize"));
    expect(screen.queryByTestId("live-fullscreen")).toBeNull();
    expect(screen.getByTestId("finish-view")).toBeInTheDocument();
    expect(document.body.style.overflow).toBe("");
  });

  it("startMaximized opens full-screen, and Esc closes it", async () => {
    await open({ startMaximized: true });
    expect(screen.getByTestId("live-fullscreen")).toBeInTheDocument();
    expect(screen.getByTestId("live-minimize")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByTestId("live-fullscreen")).toBeNull();
    expect(screen.getByTestId("live-maximize")).toBeInTheDocument();
  });

  it("a tap maps the shown position to the real page size", async () => {
    const { pic } = await open();
    fireEvent.click(pic, { clientX: 10 + 160, clientY: 100 + 50, detail: 1 }); // the middle, a quarter down
    await waitFor(() => expect(browserLive).toHaveBeenCalledTimes(1));
    expect(browserLive).toHaveBeenCalledWith({ action: "click", x: 640, y: 200 }, "finish", 7);
  });

  it("a keyboard click (no position) sends nothing", async () => {
    const { pic } = await open();
    fireEvent.click(pic, { detail: 0 });
    expect(browserLive).not.toHaveBeenCalled();
  });

  it("a vertical swipe scrolls the page (finger up = page down) and is not also a tap", async () => {
    const { pic } = await open();
    pointer("pointerDown", pic, 100, 250);
    pointer("pointerUp", pic, 102, 200); // 50 px up on a picture shown at a quarter size
    fireEvent.click(pic, { clientX: 102, clientY: 200, detail: 1 }); // the click a browser may still send
    await waitFor(() => expect(browserLive).toHaveBeenCalledTimes(1));
    expect(browserLive).toHaveBeenCalledWith({ action: "scroll", dy: 200 }, "finish", 7);

    await waitFor(() => expect(screen.getByRole("button", { name: "Scroll up" })).not.toBeDisabled());
    browserLive.mockClear();
    pointer("pointerDown", pic, 100, 200);
    pointer("pointerUp", pic, 100, 250); // finger down = page up
    await waitFor(() => expect(browserLive).toHaveBeenCalledWith({ action: "scroll", dy: -200 }, "finish", 7));
  });

  it("a small wiggle is a tap, not a swipe", async () => {
    const { pic } = await open();
    pointer("pointerDown", pic, 100, 250);
    pointer("pointerUp", pic, 101, 254);
    fireEvent.click(pic, { clientX: 101, clientY: 254, detail: 1 });
    await waitFor(() => expect(browserLive).toHaveBeenCalledTimes(1));
    expect(browserLive.mock.calls[0][0].action).toBe("click");
  });

  it("Back calls the live API with the back action (and nothing else)", async () => {
    await open();
    fireEvent.click(screen.getByTestId("live-back"));
    await waitFor(() => expect(browserLive).toHaveBeenCalledWith({ action: "back" }, "finish", 7));
  });

  it("Done hands the browser back", async () => {
    const { onDone } = await open();
    browserLive.mockResolvedValue({ status: "ok", done: true });
    fireEvent.click(screen.getByTestId("finish-done"));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
  });
});
