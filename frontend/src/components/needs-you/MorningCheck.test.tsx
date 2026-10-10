import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MorningCheck } from "./MorningCheck";
import { LAST_CHECK_KEY } from "@/lib/morning-check";

const getMorningCheck = vi.fn();
vi.mock("@/lib/api", () => ({ getMorningCheck: (...a: unknown[]) => getMorningCheck(...a) }));

const NOW = "2026-10-10T09:00:00+00:00";
const bucket = (items: ReturnType<typeof item>[] = []) => ({ count: items.length, items });
const item = (id: number, company: string, receipt: number | null = null) => ({
  application_id: id,
  receipt_id: receipt,
  company,
  title: "AI Engineer",
  at: "2026-10-10T08:00:00+00:00",
});
const payload = (state: Record<string, unknown> = {}) => ({
  now: NOW,
  since: "2026-10-09T09:00:00+00:00",
  state: {
    paused: false, paused_by: null, paused_at: null, apply_mode: "ask_each",
    submit_mode: "confirm", daily_cap: 10, applied_today: 4, ...state,
  },
  tally: {
    sent: bucket([item(5, "Mistral", 9), item(6, "DeepL", 10)]),
    blocked: bucket([item(7, "Poolside")]),
    waiting: bucket(),
    failed: bucket(),
  },
});

beforeEach(() => {
  getMorningCheck.mockReset().mockResolvedValue(payload());
  window.localStorage.clear();
});
afterEach(() => window.localStorage.clear());

describe("MorningCheck", () => {
  it("running: word, both mode labels, quota with a limit", async () => {
    render(<MorningCheck />);
    const strip = await screen.findByTestId("mc-strip");
    for (const t of ["Running", "Ask about each job", "Confirm before sending", "4 of 10 today"]) {
      expect(strip).toHaveTextContent(t);
    }
    expect(screen.getByText("Morning check")).toBeInTheDocument();
    expect(screen.queryByTestId("mc-paused-by")).toBeNull();
  });

  it("paused with no limit: '4 today', who and when", async () => {
    const paused = { paused: true, paused_by: "web", paused_at: NOW, daily_cap: null, submit_mode: "auto_when_sure" };
    getMorningCheck.mockResolvedValue(payload(paused));
    render(<MorningCheck />);
    const strip = await screen.findByTestId("mc-strip");
    expect(strip).toHaveTextContent("Paused");
    expect(strip).toHaveTextContent("Send on its own when sure");
    expect(strip.textContent).toMatch(/4 today$/);
    expect(screen.getByTestId("mc-paused-by").textContent).toMatch(/^Paused by you, /);
  });

  it("a non-zero bucket toggles its list; a zero is plain text", async () => {
    render(<MorningCheck />);
    await screen.findByTestId("mc-tally");
    expect(screen.getByTestId("tally-sent")).toHaveTextContent("Sent 2");
    expect(screen.getByTestId("tally-blocked")).toHaveTextContent("Blocked 1");
    expect(screen.queryByTestId("tally-waiting")).toBeNull();
    expect(screen.queryByTestId("tally-failed")).toBeNull();
    expect(screen.getByTestId("mc-tally")).toHaveTextContent("Waiting 0");

    fireEvent.click(screen.getByTestId("tally-sent"));
    const links = screen.getByTestId("tally-list-sent").querySelectorAll("a");
    expect(links[0]).toHaveAttribute("href", "/receipts/9");
    expect(links[1]).toHaveAttribute("href", "/receipts/10");
    expect(links[0]).toHaveTextContent("Mistral · AI Engineer");
    fireEvent.click(screen.getByTestId("tally-blocked"));
    expect(screen.queryByTestId("tally-list-sent")).toBeNull();
    expect(screen.getByTestId("tally-list-blocked").querySelector("a")).toHaveAttribute("href", "/applications/7");
    fireEvent.click(screen.getByTestId("tally-blocked"));
    expect(screen.queryByTestId("tally-list-blocked")).toBeNull();
  });

  it("first visit: last 24 hours, stamp written after render under its own key", async () => {
    render(<MorningCheck />);
    await screen.findByTestId("mc-tally");
    expect(screen.getByTestId("mc-tally")).toHaveTextContent("In the last 24 hours");
    await waitFor(() => expect(window.localStorage.getItem(LAST_CHECK_KEY)).toBe(NOW));
    expect(window.localStorage.getItem("job360-last-visit")).toBeNull();
  });

  it("a returning visit asks since the stored stamp", async () => {
    window.localStorage.setItem(LAST_CHECK_KEY, "2026-10-08T18:40:00+00:00");
    render(<MorningCheck />);
    await screen.findByTestId("mc-tally");
    expect(getMorningCheck).toHaveBeenCalledWith("2026-10-08T18:40:00+00:00");
    expect(screen.getByTestId("mc-tally")).toHaveTextContent("Since your last visit,");
  });

  it("an error says so; Try again reloads", async () => {
    getMorningCheck.mockRejectedValueOnce(new Error("down"));
    render(<MorningCheck />);
    expect(await screen.findByText("Could not load the morning check.")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("mc-retry"));
    expect(await screen.findByTestId("mc-strip")).toBeInTheDocument();
  });

  it("uses theme tokens only, in light and dark", async () => {
    for (const theme of ["", "dark"]) {
      const { container, unmount } = render(<div className={theme}><MorningCheck /></div>);
      await screen.findByTestId("mc-strip");
      expect(container.innerHTML).not.toMatch(/#[0-9a-f]{3,8}\b|bg-white|text-black/i);
      unmount();
    }
  });
});
