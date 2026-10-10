import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ReadyToSend } from "./ReadyToSend";
import { ReadyCard } from "./ReadyCard";
import { card } from "./ready-fixtures";
import { ApiError } from "@/lib/api-error";
import { dayMonth } from "@/lib/ready-to-send";

const getReadyToSend = vi.fn();
const approveSend = vi.fn();
const declineSend = vi.fn();
vi.mock("@/lib/api", () => ({
  getReadyToSend: (...a: unknown[]) => getReadyToSend(...a),
  approveSend: (...a: unknown[]) => approveSend(...a),
  declineSend: (...a: unknown[]) => declineSend(...a),
}));
vi.mock("@/lib/assistant-state", () => ({ PAUSE_CHANGED_EVENT: "job360:pause-changed" }));
const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock("sonner", () => ({ toast: { success: (m: string) => toastSuccess(m), error: (m: string) => toastError(m) } }));

const payload = (items: ReturnType<typeof card>[], paused = false) => ({
  paused,
  total: items.length,
  unflagged: items.filter((c) => c.flags.length === 0).length,
  items,
});

beforeEach(() => {
  for (const f of [getReadyToSend, approveSend, declineSend, toastSuccess, toastError]) f.mockReset();
  approveSend.mockResolvedValue({});
  declineSend.mockResolvedValue({});
});
afterEach(() => vi.clearAllMocks());

describe("Ready to send", () => {
  it("shows the card copy, the version chips and a chip for every answer source", async () => {
    getReadyToSend.mockResolvedValue(payload([card(64)]));
    render(<ReadyToSend />);
    const el = await screen.findByTestId("ready-card-64");
    expect(screen.getByText("Ready to send")).toBeInTheDocument();
    expect(el.textContent).toContain(`APP-064 · brought by Claude · ${dayMonth("2026-10-08T07:00:00+00:00", true)}`);
    expect(el).toHaveTextContent("AI Engineer, Agents");
    expect(el).toHaveTextContent("Poolside · Paris · score 82, set by Claude");
    expect(el.textContent).toMatch(/Claude prepared this \d\d:\d\d/);
    expect(el.textContent).toContain(`CV v3 · ${dayMonth("2026-10-08T08:02:00+00:00", true)}`);
    expect(el.textContent).toContain(`Cover letter v1 · ${dayMonth("2026-10-08T08:05:00+00:00", true)}`);
    expect(screen.getByRole("link", { name: "Open both" })).toHaveAttribute("href", "/applications/64#section-documents");
    expect(el).toHaveTextContent("Every answer that will go out");
    expect(el.textContent).toContain(`Memory · saved by Claude, ${dayMonth("2026-10-03T09:00:00+00:00")}`);
    expect(el).toHaveTextContent("Profile");
    expect(el).toHaveTextContent("Written new · by Claude");
    expect(el).toHaveTextContent("Guessed · by Claude");
    expect(el).toHaveTextContent("Your choice is saved with the time and this page as the place.");
  });

  it("flags show in words, with the country spelled out", async () => {
    const flags = [{ code: "missing", text: "Salary for DE not saved yet \u2014 answer it first", key: "salary.DE", country: "DE" }];
    getReadyToSend.mockResolvedValue(payload([card(7, { flags })]));
    render(<ReadyToSend />);
    expect(await screen.findByTestId("ready-flags-7")).toHaveTextContent(/Salary for Germany not saved yet \u2014 answer it first/);
  });

  it("Send and Don't send call the routes for that application, then reload", async () => {
    getReadyToSend.mockResolvedValue(payload([card(64)]));
    render(<ReadyToSend />);
    fireEvent.click(await screen.findByTestId("ready-send-64"));
    await waitFor(() => expect(approveSend).toHaveBeenCalledWith(64, { artifactId: 64, formFilledEventId: 640 }));
    await waitFor(() => expect(getReadyToSend).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByTestId("ready-decline-64"));
    await waitFor(() => expect(declineSend).toHaveBeenCalledWith(64));
    expect(toastSuccess).toHaveBeenCalledTimes(2);
  });

  it("a failed Send says so and keeps the card", async () => {
    approveSend.mockRejectedValue(new Error("no"));
    getReadyToSend.mockResolvedValue(payload([card(64)]));
    render(<ReadyToSend />);
    fireEvent.click(await screen.findByTestId("ready-send-64"));
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(screen.getByTestId("ready-card-64")).toBeInTheDocument();
  });

  it("a card that changed since it loaded (409) says so and reloads", async () => {
    approveSend.mockRejectedValue(new ApiError(409, "the CV changed since you looked - look again"));
    getReadyToSend.mockResolvedValue(payload([card(64)]));
    render(<ReadyToSend />);
    fireEvent.click(await screen.findByTestId("ready-send-64"));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("This changed since you looked. Check it again."));
    await waitFor(() => expect(getReadyToSend).toHaveBeenCalledTimes(2));
  });

  it("paused: the label says on hold and Send is disabled", async () => {
    getReadyToSend.mockResolvedValue(payload([card(64)], true));
    render(<ReadyToSend />);
    expect(await screen.findByText("Ready to send · on hold while paused")).toBeInTheDocument();
    expect(screen.getByTestId("ready-send-64")).toBeDisabled();
    expect(screen.getByTestId("ready-decline-64")).not.toBeDisabled();
  });

  it("nothing waiting: no section at all", async () => {
    getReadyToSend.mockResolvedValue(payload([]));
    const { container } = render(<ReadyToSend />);
    await waitFor(() => expect(getReadyToSend).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("a failed read says so and retries", async () => {
    getReadyToSend.mockRejectedValueOnce(new Error("down")).mockResolvedValue(payload([card(1)]));
    render(<ReadyToSend />);
    expect(await screen.findByText("Could not load Ready to send.")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("ready-retry"));
    expect(await screen.findByTestId("ready-card-1")).toBeInTheDocument();
  });

  it("reports the total for the badge", async () => {
    getReadyToSend.mockResolvedValue(payload([card(1), card(2)]));
    const onTotal = vi.fn();
    render(<ReadyToSend onTotal={onTotal} />);
    await waitFor(() => expect(onTotal).toHaveBeenCalledWith(2));
  });

  it("uses theme tokens only, in light and dark", () => {
    for (const theme of ["", "dark"]) {
      const { container, unmount } = render(
        <div className={theme}>
          <ReadyCard card={card(64)} paused={false} onChanged={() => undefined} />
        </div>,
      );
      expect(container.innerHTML).not.toMatch(/#[0-9a-f]{3,8}\b|bg-white|text-black/i);
      unmount();
    }
  });
});
