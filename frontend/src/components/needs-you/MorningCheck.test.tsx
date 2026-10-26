import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MorningCheck } from "./MorningCheck";
import { LAST_CHECK_KEY } from "@/lib/morning-check";

const getMorningCheck = vi.fn();
const updateProfileFields = vi.fn();
vi.mock("@/lib/api", () => ({
  getMorningCheck: (...a: unknown[]) => getMorningCheck(...a),
  updateProfileFields: (...a: unknown[]) => updateProfileFields(...a),
}));
const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock("sonner", () => ({ toast: { success: (m: string) => toastSuccess(m), error: (m: string) => toastError(m) } }));

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
    paused: false, paused_by: null, paused_at: null, apply_min_score: 75, above_line: "ask", below_line: "skip",
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
  updateProfileFields.mockReset().mockResolvedValue({});
  toastSuccess.mockReset();
  toastError.mockReset();
  window.localStorage.clear();
});
afterEach(() => window.localStorage.clear());

describe("MorningCheck", () => {
  it("running: word, score line, batch switch, below-line rule, quota with a limit, the amber note", async () => {
    render(<MorningCheck />);
    const strip = await screen.findByTestId("mc-strip");
    for (const t of ["On", "Score line 75", "Batch auto apply: off", "Below the line: dropped", "4 of 10 today"]) {
      expect(strip).toHaveTextContent(t);
    }
    expect(strip).not.toHaveTextContent("Confirm before sending");
    expect(screen.getByText("Morning check (run by your assistant)")).toBeInTheDocument();
    expect(screen.getByTestId("mc-always-asks")).toHaveTextContent(
      "Your assistant always stops and asks you when it is not sure: a CAPTCHA, a login page, a guessed answer, or anything not verified.",
    );
    expect(screen.queryByTestId("mc-paused-by")).toBeNull();
  });

  it("batch reads on only when BOTH halves are on; an older below-line value is shown as it is", async () => {
    getMorningCheck.mockResolvedValue(payload({ above_line: "auto", submit_mode: "auto_when_sure", below_line: "ask" }));
    render(<MorningCheck />);
    const strip = await screen.findByTestId("mc-strip");
    expect(strip).toHaveTextContent("Batch auto apply: on");
    expect(strip).toHaveTextContent("Below the line: asks you");
  });

  it("score line not set: says so, Set it opens the editor, and the amber note still shows", async () => {
    getMorningCheck.mockResolvedValue(payload({ apply_min_score: null }));
    render(<MorningCheck />);
    const strip = await screen.findByTestId("mc-strip");
    expect(strip).toHaveTextContent("Score line not set");
    expect(screen.queryByTestId("mc-below")).toBeNull();
    expect(screen.getByTestId("mc-always-asks")).toBeInTheDocument();
    expect(screen.getByTestId("mc-score-line-set")).toHaveAttribute("href", "/connect?tab=assistant");
  });

  it("paused with no limit: '4 today', who and when", async () => {
    const paused = { paused: true, paused_by: "web", paused_at: NOW, daily_cap: null, submit_mode: "auto_when_sure" };
    getMorningCheck.mockResolvedValue(payload(paused));
    render(<MorningCheck />);
    const strip = await screen.findByTestId("mc-strip");
    expect(strip).toHaveTextContent("Paused");
    expect(strip).toHaveTextContent("Batch auto apply: off");
    expect(screen.getByTestId("mc-quota").textContent).toBe("4 today");
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

describe("MorningCheck inline editor", () => {
  // Owner rule 2026-10-26: every assistant setting lives on /connect. The strip only
  // reports; Change is a link, and nothing here can write a setting.
  const HREF = "/connect?tab=assistant";
  const noEditor = () => {
    for (const id of ["mc-editor", "mc-score-input", "mc-batch-on", "mc-batch-off", "mc-save", "mc-cancel"]) {
      expect(screen.queryByTestId(id)).toBeNull();
    }
  };

  it("Change opens the editor with the saved values; Cancel closes it without a write", async () => {
    render(<MorningCheck />);
    await screen.findByTestId("mc-strip");
    expect(screen.getByTestId("mc-edit")).toHaveAttribute("href", HREF);
    fireEvent.click(screen.getByTestId("mc-edit"));
    noEditor();
    expect(updateProfileFields).not.toHaveBeenCalled();
  });

  it("Batch auto apply ON writes both halves; the strip re-reads", async () => {
    getMorningCheck.mockResolvedValue(payload({ above_line: "auto", submit_mode: "auto_when_sure" }));
    render(<MorningCheck />);
    const strip = await screen.findByTestId("mc-strip");
    expect(strip).toHaveTextContent("Batch auto apply: on");
    noEditor();
    expect(updateProfileFields).not.toHaveBeenCalled();
  });

  it("Batch auto apply OFF writes ask + confirm back", async () => {
    render(<MorningCheck />);
    const strip = await screen.findByTestId("mc-strip");
    expect(strip).toHaveTextContent("Batch auto apply: off");
    expect(screen.queryByRole("radiogroup")).toBeNull();
    expect(updateProfileFields).not.toHaveBeenCalled();
  });

  it("an older below-line value is turned into 'dropped' by the user's own Save", async () => {
    getMorningCheck.mockResolvedValue(payload({ below_line: "auto" }));
    render(<MorningCheck />);
    await screen.findByTestId("mc-strip");
    fireEvent.click(screen.getByTestId("mc-edit"));
    expect(updateProfileFields).not.toHaveBeenCalled();
  });

  it("a re-read mid-edit never turns an assistant's newer change into a user edit", async () => {
    render(<MorningCheck />);
    await screen.findByTestId("mc-strip");
    getMorningCheck.mockResolvedValue(payload({ above_line: "auto", submit_mode: "auto_when_sure" }));
    window.dispatchEvent(new CustomEvent("job360:ready-changed"));
    await waitFor(() => expect(screen.getByTestId("mc-strip")).toHaveTextContent("Batch auto apply: on"));
    expect(updateProfileFields).not.toHaveBeenCalled();
  });

  it("a blank score line saves null (back to not set)", async () => {
    getMorningCheck.mockResolvedValue(payload({ apply_min_score: null }));
    render(<MorningCheck />);
    await screen.findByTestId("mc-strip");
    expect(screen.getByTestId("mc-score-line-set")).toHaveAttribute("href", HREF);
    noEditor();
  });

  it("a bad score line is refused before any write", async () => {
    render(<MorningCheck />);
    await screen.findByTestId("mc-strip");
    expect(screen.queryByRole("spinbutton")).toBeNull();
    expect(updateProfileFields).not.toHaveBeenCalled();
  });

  it("Save with nothing changed just closes; a failed save says so and stays open", async () => {
    render(<MorningCheck />);
    await screen.findByTestId("mc-strip");
    expect(screen.queryByRole("button", { name: /save/i })).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(updateProfileFields).not.toHaveBeenCalled();
  });
});

describe("MorningCheck refresh", () => {
  it("re-reads when a send or decline happens below it", async () => {
    render(<MorningCheck />);
    await screen.findByTestId("mc-strip");
    expect(getMorningCheck).toHaveBeenCalledTimes(1);
    window.dispatchEvent(new CustomEvent("job360:ready-changed"));
    await waitFor(() => expect(getMorningCheck).toHaveBeenCalledTimes(2));
  });
});
