import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { BatchApplyPanel } from "./BatchApplyPanel";
import { NeedsYou } from "./NeedsYou";

const getAssistantSettings = vi.fn();
const updateProfileFields = vi.fn();
const getProfile = vi.fn();
const listApplications = vi.fn();
const recordApplicationEvent = vi.fn();
const recordApplicationReceipt = vi.fn();
const resolveSubmit = vi.fn();
const fetchWhatsNewPages = vi.fn();
const listAsks = vi.fn();

vi.mock("@/lib/api", () => ({
  ASKS_CHANGED_EVENT: "job360:asks-changed",
  getAssistantSettings: (...a: unknown[]) => getAssistantSettings(...a),
  updateProfileFields: (...a: unknown[]) => updateProfileFields(...a),
  getProfile: (...a: unknown[]) => getProfile(...a),
  listApplications: (...a: unknown[]) => listApplications(...a),
  recordApplicationEvent: (...a: unknown[]) => recordApplicationEvent(...a),
  recordApplicationReceipt: (...a: unknown[]) => recordApplicationReceipt(...a),
  resolveSubmit: (...a: unknown[]) => resolveSubmit(...a),
  listAsks: (...a: unknown[]) => listAsks(...a),
}));
vi.mock("@/lib/home-fetch", () => ({ fetchWhatsNewPages: (...a: unknown[]) => fetchWhatsNewPages(...a) }));
vi.mock("./MorningCheck", () => ({ MorningCheck: () => null }));
vi.mock("./ReadyToSend", () => ({ ReadyToSend: () => null }));
const toastSuccess = vi.fn();
vi.mock("sonner", () => ({ toast: { success: (m: string) => toastSuccess(m), error: vi.fn() } }));

const field = (value: unknown, effective: unknown = value) => ({ value, effective });
const view = (over: Record<string, unknown> = {}) => ({
  apply_min_score: field(null, null),
  above_line: field("", "ask"),
  below_line: field("", "ask"),
  submit_mode: field("", "confirm"),
  daily_cap: field(null, null),
  waiting: [],
  ...over,
});
const ev = (id: number, application_id: number, event_type: string) => ({
  id, application_id, event_type, occurred_at: "2026-10-17T10:00:00Z", payload: {}, corrects_event_id: null,
});
const queued = {
  applications: [
    { id: 11, queue_position: 1, job_title: "Data Analyst", job_company: "Acme", preflight: { has_cv: false, kit_missing: ["a", "b"] } },
    { id: 12, queue_position: 2, job_title: "Engineer", job_company: "Beta", preflight: {} },
  ],
  total: 2,
  queue_summary: { queued: 2, ready_to_send: 1, asked: 1, blocked: 0, daily_left: 3 },
};

beforeEach(() => {
  getAssistantSettings.mockReset().mockResolvedValue(view());
  updateProfileFields.mockReset().mockResolvedValue({});
  getProfile.mockReset().mockResolvedValue({ preferences: { avoid_companies: ["Evil Corp"] } });
  listApplications.mockReset().mockResolvedValue(queued);
  recordApplicationEvent.mockReset().mockResolvedValue({});
  recordApplicationReceipt.mockReset().mockResolvedValue({});
  resolveSubmit.mockReset().mockResolvedValue({});
  fetchWhatsNewPages
    .mockReset()
    .mockResolvedValue({ events: [ev(1, 5, "submit_attempted")], applications: [{ id: 5, job_title: "Analyst", job_company: "Gamma" }] });
  toastSuccess.mockReset();
});

describe("BatchApplyPanel", () => {
  it("is titled Batch apply and has the three cards", async () => {
    render(<BatchApplyPanel />);
    const panel = screen.getByTestId("batch-apply-panel");
    expect(panel).toHaveTextContent("Batch apply");
    await screen.findByTestId("daily-cap");
    await screen.findByTestId("batch-row-11");
    await screen.findByTestId("started-5");
    expect(screen.getByTestId("batch-rules")).toHaveTextContent("Your rules");
    expect(screen.getByTestId("batch-queue")).toHaveTextContent("Queue");
    expect(screen.getByTestId("started-not-confirmed")).toHaveTextContent("Started, not confirmed");
  });

  it("scrolls with the page and puts what needs the user first", () => {
    render(<BatchApplyPanel />);
    const panel = screen.getByTestId("batch-apply-panel");
    // A sticky panel taller than the screen hid the queue (real-browser check, 2026-10-18).
    expect(panel.className).not.toMatch(/sticky|overflow-y-auto|max-h-/);
    expect(panel.className).toContain("min-w-0");
    const html = panel.innerHTML;
    expect(html.indexOf("batch-queue")).toBeLessThan(html.indexOf("batch-rules"));
  });

  it("is placed beside the main column on the Needs-you page", async () => {
    listAsks.mockResolvedValue({ asks: [], open_count: 0 });
    render(<NeedsYou />);
    const layout = await screen.findByTestId("needs-you-layout");
    expect(layout.className).toContain("xl:grid-cols-");
    expect(within(layout).getByTestId("needs-you-main")).toBeInTheDocument();
    expect(within(layout).getByTestId("batch-apply-panel")).toBeInTheDocument();
  });

  describe("Your rules", () => {
    it("shows empty rules as not set and the never-apply list", async () => {
      render(<BatchApplyPanel />);
      const cap = (await screen.findByTestId("daily-cap")) as HTMLInputElement;
      expect(cap.value).toBe("");
      expect(cap.placeholder).toBe("No limit");
      expect(await screen.findByTestId("batch-never-apply")).toHaveTextContent("Evil Corp");
      expect(screen.getByTestId("batch-never-apply-link")).toHaveAttribute("href", "/profile");
    });

    it("saves the daily limit at once", async () => {
      render(<BatchApplyPanel />);
      const cap = await screen.findByTestId("daily-cap");
      fireEvent.change(cap, { target: { value: "7" } });
      fireEvent.blur(cap);
      await waitFor(() => expect(updateProfileFields).toHaveBeenCalledTimes(1));
      expect(updateProfileFields.mock.calls[0][0]).toEqual([{ path: "assistant_settings.daily_cap", value: 7 }]);
      await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith("Saved."));
    });

    it("starts auto for a while after one confirm", async () => {
      render(<BatchApplyPanel />);
      fireEvent.change(await screen.findByTestId("auto-grant-count"), { target: { value: "3" } });
      const later = new Date(Date.now() + 2 * 3600_000);
      const pad = (n: number) => String(n).padStart(2, "0");
      const local = `${later.getFullYear()}-${pad(later.getMonth() + 1)}-${pad(later.getDate())}T${pad(later.getHours())}:${pad(later.getMinutes())}`;
      fireEvent.change(screen.getByTestId("auto-grant-until"), { target: { value: local } });
      fireEvent.click(screen.getByTestId("auto-grant-save"));
      fireEvent.click(screen.getByTestId("auto-grant-confirm"));
      await waitFor(() => expect(updateProfileFields).toHaveBeenCalled());
      expect(updateProfileFields.mock.calls[0][0]).toHaveLength(2);
    });

    it("shows a retry when the rules cannot load", async () => {
      getAssistantSettings.mockRejectedValueOnce(new Error("down"));
      render(<BatchApplyPanel />);
      fireEvent.click(await screen.findByTestId("batch-rules-retry"));
      await screen.findByTestId("daily-cap");
    });
  });

  describe("Queue", () => {
    it("shows the summary, each job with its problems in plain words", async () => {
      render(<BatchApplyPanel />);
      expect(await screen.findByTestId("batch-queue-summary")).toHaveTextContent("2 queued · 1 ready · 1 need you · 3 left today");
      const row = screen.getByTestId("batch-row-11");
      expect(row).toHaveTextContent("#1");
      expect(row).toHaveTextContent("Data Analyst · Acme");
      expect(within(row).getByRole("link")).toHaveAttribute("href", "/applications/11");
      expect(screen.getByTestId("batch-row-problems-11")).toHaveTextContent("Missing 2 answers");
      expect(screen.getByTestId("batch-row-problems-11")).toHaveTextContent("No CV yet");
      expect(screen.queryByTestId("batch-row-problems-12")).toBeNull();
    });

    it("says so when the batch is empty", async () => {
      listApplications.mockResolvedValue({ applications: [], total: 0, queue_summary: null });
      render(<BatchApplyPanel />);
      expect(await screen.findByTestId("batch-queue-empty")).toHaveTextContent(
        "No jobs in your batch. Add one from any application page with Add to batch.",
      );
    });

    it("Remove records unqueued and reloads", async () => {
      render(<BatchApplyPanel />);
      fireEvent.click(await screen.findByTestId("batch-remove-11"));
      await waitFor(() =>
        expect(recordApplicationEvent).toHaveBeenCalledWith(11, { event_type: "unqueued", payload: { reason: "user_removed" } }),
      );
      await waitFor(() => expect(listApplications).toHaveBeenCalledTimes(2));
    });
  });

  describe("Started, not confirmed", () => {
    it("'It was sent' records the application after a confirm, then refreshes", async () => {
      render(<BatchApplyPanel />);
      fireEvent.click(await screen.findByTestId("submit-was-sent-5"));
      fireEvent.click(screen.getByTestId("submit-sent-confirm-5"));
      await waitFor(() => expect(recordApplicationReceipt).toHaveBeenCalledWith(5, {}));
      await waitFor(() => expect(fetchWhatsNewPages).toHaveBeenCalledTimes(2));
    });

    it("'It was not sent' closes the stop, then refreshes", async () => {
      render(<BatchApplyPanel />);
      fireEvent.click(await screen.findByTestId("submit-not-sent-5"));
      await waitFor(() => expect(resolveSubmit).toHaveBeenCalledWith(5));
      await waitFor(() => expect(fetchWhatsNewPages).toHaveBeenCalledTimes(2));
    });

    it("is hidden when nothing is waiting", async () => {
      fetchWhatsNewPages.mockResolvedValue({ events: [], applications: [] });
      render(<BatchApplyPanel />);
      await waitFor(() => expect(fetchWhatsNewPages).toHaveBeenCalled());
      expect(screen.queryByTestId("started-not-confirmed")).toBeNull();
    });
  });
});
