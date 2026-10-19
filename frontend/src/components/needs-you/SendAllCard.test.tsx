import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ReadyToSend } from "./ReadyToSend";
import { guardSend, splitReady } from "@/lib/ready-to-send";
import { card } from "./ready-fixtures";
import { ApiError } from "@/lib/api-error";

const getReadyToSend = vi.fn();
const approveSend = vi.fn();
vi.mock("@/lib/api", () => ({
  getReadyToSend: (...a: unknown[]) => getReadyToSend(...a),
  approveSend: (...a: unknown[]) => approveSend(...a),
  declineSend: vi.fn(),
}));
vi.mock("@/lib/assistant-state", () => ({ PAUSE_CHANGED_EVENT: "job360:pause-changed", READY_CHANGED_EVENT: "job360:ready-changed" }));
const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock("sonner", () => ({ toast: { success: (m: string) => toastSuccess(m), error: (m: string) => toastError(m) } }));

const missing = [{ code: "missing", text: "Salary for DE not saved yet — answer it first", key: "salary.DE", country: "DE" }];
const list = (paused = false, items = [card(1), card(2), card(3), card(4, { job_company: "DeepL", job_title: "AI Engineer", job_location: "Cologne", flags: missing })]) => ({
  paused,
  total: items.length,
  unflagged: items.filter((c) => c.flags.length === 0).length,
  items,
});

beforeEach(() => {
  getReadyToSend.mockReset().mockResolvedValue(list());
  approveSend.mockReset().mockResolvedValue({});
  toastSuccess.mockReset();
  toastError.mockReset();
});

describe("Send all unflagged", () => {
  it("lists the clean rows, holds the flagged one out with its reason and an Answer link", async () => {
    render(<ReadyToSend />);
    const box = await screen.findByTestId("send-all-card");
    expect(box).toHaveTextContent("Send all unflagged (3)");
    expect(box).toHaveTextContent("These 3 go out exactly as listed. Open any row to see every answer.");
    expect(screen.getByTestId("send-all-row-1")).toHaveTextContent("CV v3 · letter v1 · 4 answers");
    expect(screen.queryByTestId("send-all-row-4")).toBeNull();
    const held = screen.getByTestId("held-row-4");
    expect(held).toHaveTextContent("Held out · DeepL · AI Engineer · Cologne");
    expect(held).toHaveTextContent("Salary for Germany not saved yet — answer it first");
    expect(screen.getByTestId("held-answer-4")).toHaveAttribute("href", "/profile?tab=memory");
  });

  it("needs a confirm; Cancel sends nothing", async () => {
    render(<ReadyToSend />);
    fireEvent.click(await screen.findByTestId("send-all-open"));
    expect(screen.getByTestId("send-all-confirm")).toHaveTextContent("Send these 3 now? Each one is saved as your yes.");
    fireEvent.click(screen.getByTestId("send-all-no"));
    expect(approveSend).not.toHaveBeenCalled();
  });

  it("Send approves each clean application on its own, never the held one", async () => {
    render(<ReadyToSend />);
    fireEvent.click(await screen.findByTestId("send-all-open"));
    fireEvent.click(screen.getByTestId("send-all-yes"));
    await waitFor(() => expect(approveSend).toHaveBeenCalledTimes(3));
    expect(approveSend.mock.calls.map((c) => c[0])).toEqual([1, 2, 3]);
    expect(toastSuccess).toHaveBeenCalledWith("Sent 3.");
  });

  it("re-reads before sending and skips a row whose fill or CV changed, naming it", async () => {
    render(<ReadyToSend />);
    fireEvent.click(await screen.findByTestId("send-all-open"));
    const changed = [
      card(1),
      card(2, { form_filled_event_id: 999, job_company: "Mistral" }),
      card(3, { cv: { artifact_id: 777, version: 4, saved_at: "2026-10-08T09:00:00+00:00", made_by: "web" }, job_company: "Sana" }),
    ];
    getReadyToSend.mockResolvedValue(list(false, changed));
    fireEvent.click(screen.getByTestId("send-all-yes"));
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
    expect(approveSend.mock.calls.map((c) => c[0])).toEqual([1]);
    expect(toastSuccess).toHaveBeenCalledWith("Sent 1. Skipped (changed since you looked): Poolside, Poolside.");
  });

  it("passes the CV and fill the user saw; a 409 is skipped, a failure is named as not saved", async () => {
    render(<ReadyToSend />);
    fireEvent.click(await screen.findByTestId("send-all-open"));
    approveSend.mockImplementation((id: number) =>
      id === 2 ? Promise.reject(new ApiError(409, "changed")) : id === 3 ? Promise.reject(new Error("down")) : Promise.resolve({}),
    );
    fireEvent.click(screen.getByTestId("send-all-yes"));
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(approveSend).toHaveBeenCalledWith(1, { artifactId: 1, formFilledEventId: 10 });
    expect(toastError).toHaveBeenCalledWith(
      "Sent 1. Skipped (changed since you looked): Poolside. Not saved, try again: Poolside.",
    );
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it("paused: the button is disabled", async () => {
    getReadyToSend.mockResolvedValue(list(true));
    render(<ReadyToSend />);
    expect(await screen.findByTestId("send-all-open")).toBeDisabled();
  });

  it("appears only when two or more are unflagged", async () => {
    getReadyToSend.mockResolvedValue(list(false, [card(1)]));
    render(<ReadyToSend />);
    await screen.findByTestId("ready-card-1");
    expect(screen.queryByTestId("send-all-card")).toBeNull();
  });
});

describe("ready-to-send helpers", () => {
  it("splitReady and guardSend", () => {
    const a = card(1);
    const b = card(2, { flags: missing });
    expect(splitReady([a, b])).toEqual({ clean: [a], held: [b] });
    expect(guardSend([a], [a]).go).toEqual([a]);
    expect(guardSend([a], []).skipped).toEqual([a]);
    expect(guardSend([a], [card(1, { flags: missing })]).skipped).toEqual([a]);
  });
});

describe("held-out reasons and the morning check refresh", () => {
  const two = [
    ...missing,
    { code: "guessed", text: "An answer was guessed: Visa?" },
    { code: "no_cv", text: "No CV saved for this application" },
  ];
  const heldList = () =>
    list(false, [card(1), card(2), card(4, { job_company: "DeepL", job_title: "AI Engineer", job_location: "Cologne", flags: two })]);

  it("shows the first reason and 'and 2 more'; expanding lists every reason", async () => {
    getReadyToSend.mockResolvedValue(heldList());
    render(<ReadyToSend />);
    const held = await screen.findByTestId("held-row-4");
    expect(held).toHaveTextContent("Salary for Germany not saved yet");
    expect(held).not.toHaveTextContent("An answer was guessed: Visa?");
    fireEvent.click(screen.getByTestId("held-more-4"));
    const all = screen.getByTestId("held-reasons-4");
    expect(all).toHaveTextContent("Salary for Germany not saved yet");
    expect(all).toHaveTextContent("An answer was guessed: Visa?");
    expect(all).toHaveTextContent("No CV saved for this application");
  });

  it("one reason: no 'and N more'", async () => {
    render(<ReadyToSend />);
    await screen.findByTestId("held-row-4");
    expect(screen.queryByTestId("held-more-4")).toBeNull();
  });

  it("a send tells the morning check strip to re-read, without a reload", async () => {
    const seen = vi.fn();
    window.addEventListener("job360:ready-changed", seen);
    render(<ReadyToSend />);
    fireEvent.click(await screen.findByTestId("send-all-open"));
    fireEvent.click(screen.getByTestId("send-all-yes"));
    await waitFor(() => expect(seen).toHaveBeenCalled());
    window.removeEventListener("job360:ready-changed", seen);
  });
});
