import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { NeedsYou } from "./NeedsYou";
import { SettingRequestCard, describeSettingRequest, grantScopeLine } from "./SettingRequestCard";

const confirmSettingRequest = vi.fn();
const declineSettingRequest = vi.fn();
const getAssistantSettings = vi.fn();
const listAsks = vi.fn();
const toastError = vi.fn();

vi.mock("@/lib/api", () => ({
  ASKS_CHANGED_EVENT: "job360:asks-changed",
  listAsks: (...a: unknown[]) => listAsks(...a),
  answerAsk: vi.fn(),
  withdrawAsk: vi.fn(),
  getAssistantSettings: (...a: unknown[]) => getAssistantSettings(...a),
  confirmSettingRequest: (...a: unknown[]) => confirmSettingRequest(...a),
  declineSettingRequest: (...a: unknown[]) => declineSettingRequest(...a),
  getReadyToSend: async () => ({ paused: false, total: 0, unflagged: 0, items: [] }),
  getCvToApprove: async () => ({ items: [] }),
  listPeople: async () => ({ people: [] }),
}));
vi.mock("./OutcomeSuggestions", () => ({ OutcomeSuggestions: () => null }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: (...a: unknown[]) => toastError(...a) } }));

const evil = "<img src=x onerror=alert(1)>";
const request = {
  id: 11,
  path: "assistant_settings.above_line",
  value: "auto",
  requested_by: evil,
  requested_at: new Date().toISOString(),
  expires_at: new Date(Date.now() + 86_400_000).toISOString(),
  status: "waiting",
};

beforeEach(() => {
  confirmSettingRequest.mockReset().mockResolvedValue({});
  declineSettingRequest.mockReset().mockResolvedValue({});
  getAssistantSettings.mockReset().mockResolvedValue({ waiting: [request] });
  listAsks.mockReset().mockResolvedValue({ asks: [], open_count: 0 });
  toastError.mockReset();
});

describe("describeSettingRequest", () => {
  it("explains each gated path in plain words", () => {
    expect(describeSettingRequest("assistant_settings.above_line", "auto", 80).change).toBe(
      "Apply on its own for jobs at or above your score line (80)",
    );
    expect(describeSettingRequest("assistant_settings.above_line", "auto", null).change).toBe(
      "Apply on its own for jobs at or above your score line (not set yet)",
    );
    expect(describeSettingRequest("assistant_settings.below_line", "auto", 80).change).toBe(
      "Apply on its own for jobs below your score line (80)",
    );
    expect(describeSettingRequest("assistant_settings.below_line", "auto").change).toBe(
      "Apply on its own for jobs below your score line (not set yet)",
    );
    expect(describeSettingRequest("assistant_settings.submit_mode", "auto_when_sure").change).toMatch(/on its own/);
    expect(describeSettingRequest("assistant_settings.apply_min_score", 60, 75).change).toBe(
      "Lower your score line from 75 to 60",
    );
    expect(describeSettingRequest("assistant_settings.apply_min_score", 80, 75).change).toBe(
      "Raise your score line from 75 to 80",
    );
    expect(describeSettingRequest("assistant_settings.apply_min_score", 60, null).change).toBe(
      "Set your score line to 60",
    );
    expect(describeSettingRequest("assistant_settings.daily_cap", null).change).toBe("No daily limit");
    expect(describeSettingRequest("assistant_settings.daily_cap", 5).change).toBe("Up to 5 applications a day");
    expect(describeSettingRequest("assistant_settings.paused_until", "").change).toBe("Start applying again");
    expect(describeSettingRequest("preferences.daily_check", "auto").title).toBe("Your inbox");
  });

  it("outreach send: auto says it sends in your name; draft_only says you send; the Gmail mode never mentions outreach", () => {
    const on = describeSettingRequest("assistant_settings.outreach_send", "auto");
    expect(on.title).toBe("Automatic outreach emails");
    expect(on.change).toContain("in your name");
    expect(describeSettingRequest("assistant_settings.outreach_send", "draft_only").change).toBe(
      "Only draft outreach emails; I send them myself",
    );
    expect(describeSettingRequest("preferences.daily_check", "auto").change).not.toMatch(/outreach/i);
  });

  it("job hunt: a number says how often; empty says it stops", () => {
    const on = describeSettingRequest("assistant_settings.hunt_every_hours", 6);
    expect(on.title).toBe("Job hunt");
    expect(on.change).toBe("Let your assistant hunt for jobs on its own every 6 hours");
    expect(describeSettingRequest("assistant_settings.hunt_every_hours", 1).change).toMatch(/every 1 hour$/);
    expect(describeSettingRequest("assistant_settings.hunt_every_hours", null).change).toBe("Stop the scheduled job hunt");
  });

  it("people hunt: on says you still send; off says it stops", () => {
    const on = describeSettingRequest("assistant_settings.people_hunt", true);
    expect(on.title).toBe("People hunt");
    expect(on.change).toMatch(/you still send every message/);
    expect(describeSettingRequest("assistant_settings.people_hunt", false).change).toBe("Stop the people hunt");
  });

  it("says EVERY job plainly when the auto side covers all of them", () => {
    expect(describeSettingRequest("assistant_settings.above_line", "auto", 0).change).toBe(
      "Apply on its own for EVERY job (your score line is 0)",
    );
    expect(describeSettingRequest("assistant_settings.below_line", "auto", 100).change).toBe(
      "Apply on its own for EVERY job (your score line is 100)",
    );
    // the other side at those lines, and "ask", stay as before
    expect(describeSettingRequest("assistant_settings.below_line", "auto", 0).change).toBe(
      "Apply on its own for jobs below your score line (0)",
    );
    expect(describeSettingRequest("assistant_settings.above_line", "ask", 0).change).toBe(
      "Ask me about jobs at or above my score line (0)",
    );
  });
});

describe("SettingRequestCard", () => {
  it("shows the change as literal text and says nothing happens until Confirm", () => {
    const { container } = render(
      <SettingRequestCard request={{ ...request, current_score_line: 80 }} onChanged={async () => {}} />,
    );
    expect(screen.getByTestId("setting-request-change")).toHaveTextContent(
      "Apply on its own for jobs at or above your score line (80)",
    );
    expect(screen.getByText(/Nothing changes until you confirm/)).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull(); // the actor name is text, never HTML
    expect(confirmSettingRequest).not.toHaveBeenCalled();
  });

  it("Confirm applies the request and reloads", async () => {
    const onChanged = vi.fn().mockResolvedValue(undefined);
    render(<SettingRequestCard request={request} onChanged={onChanged} />);
    fireEvent.click(screen.getByTestId("setting-request-confirm"));
    await waitFor(() => expect(confirmSettingRequest).toHaveBeenCalledWith(11));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(declineSettingRequest).not.toHaveBeenCalled();
  });

  it("a confirmed resume tells the top bar and banner at once (S5b)", async () => {
    const seen: unknown[] = [];
    const on = (e: Event) => seen.push((e as CustomEvent).detail);
    window.addEventListener("job360:pause-changed", on);
    confirmSettingRequest.mockResolvedValue({ paused: false, waiting: [] });
    render(
      <SettingRequestCard
        request={{ ...request, path: "assistant_settings.paused_until", value: "" }}
        onChanged={vi.fn().mockResolvedValue(undefined)}
      />,
    );
    fireEvent.click(screen.getByTestId("setting-request-confirm"));
    await waitFor(() => expect(seen).toEqual([false]));
    window.removeEventListener("job360:pause-changed", on);
  });

  it("Don't change declines it", async () => {
    const onChanged = vi.fn().mockResolvedValue(undefined);
    render(<SettingRequestCard request={request} onChanged={onChanged} />);
    fireEvent.click(screen.getByTestId("setting-request-decline"));
    await waitFor(() => expect(declineSettingRequest).toHaveBeenCalledWith(11));
    expect(confirmSettingRequest).not.toHaveBeenCalled();
  });

  it("a failed confirm shows an error and keeps the card", async () => {
    confirmSettingRequest.mockRejectedValue(new Error("409"));
    const onChanged = vi.fn();
    render(<SettingRequestCard request={request} onChanged={onChanged} />);
    fireEvent.click(screen.getByTestId("setting-request-confirm"));
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(onChanged).not.toHaveBeenCalled();
    expect(screen.getByTestId("setting-request-11")).toBeInTheDocument();
  });
});

describe("Needs-you page", () => {
  it("lists the waiting requests and tells the badge asks + waiting", async () => {
    const seen: number[] = [];
    const on = (e: Event) => seen.push((e as CustomEvent).detail);
    window.addEventListener("job360:asks-changed", on);
    const q = (id: number) => ({
      id, application_id: null, question: `Q${id}?`, context: "", asked_by: "x", asked_at: new Date().toISOString(),
      status: "open", answer: null, withdrawn_at: null,
    });
    listAsks.mockImplementation(async (st: string) => (st === "open" ? { asks: [q(1), q(2)], open_count: 2 } : { asks: [], open_count: 2 }));
    render(<NeedsYou />);
    expect(await screen.findByTestId("setting-request-11")).toBeInTheDocument();
    expect(screen.getByText("Waiting for your OK")).toBeInTheDocument();
    await waitFor(() => expect(seen).toEqual([3])); // 2 open questions with no job + 1 waiting
    // after the user confirms, the reload comes back empty and the badge follows
    getAssistantSettings.mockResolvedValue({ waiting: [] });
    fireEvent.click(screen.getByTestId("setting-request-confirm"));
    await waitFor(() => expect(seen).toEqual([3, 2]));
    expect(screen.queryByTestId("setting-request-11")).toBeNull();
    window.removeEventListener("job360:asks-changed", on);
  });

  it("a failed settings read never hides the questions", async () => {
    getAssistantSettings.mockRejectedValue(new Error("down"));
    render(<NeedsYou />);
    expect(await screen.findByText("Nothing needs you right now.")).toBeInTheDocument();
    expect(screen.queryByText("Waiting for your OK")).toBeNull();
  });
});

describe("describeSettingRequest - pace and auto run", () => {
  it("says each new setting in plain words", () => {
    expect(describeSettingRequest("assistant_settings.company_cap", 3).change).toBe("At most 3 applications per company");
    expect(describeSettingRequest("assistant_settings.company_cap_days", null).change).toBe(
      "Count applications to one company over the standard window",
    );
    expect(describeSettingRequest("assistant_settings.weekly_cap", null).change).toBe("No weekly limit");
    expect(describeSettingRequest("assistant_settings.min_gap_minutes", 20).change).toBe("Wait at least 20 minutes between applications");
    expect(describeSettingRequest("assistant_settings.apply_hours", { from: "09:00", to: "17:00" }).change).toBe("Only apply between 09:00 and 17:00");
    expect(describeSettingRequest("assistant_settings.auto_grant_count", 5).change).toBe("Apply on its own for my next 5 applications");
  });
});

describe("grantScopeLine (slice 30)", () => {
  it("says a confirmed grant covers only the queue as it is now", () => {
    const queue = { count: 2, hosts: ["jobs.lever.co"], no_host: 0 };
    expect(grantScopeLine("assistant_settings.auto_grant_count", 5, queue)).toBe(
      "Covers only the 2 applications (sites: jobs.lever.co) in your queue now; anything queued later still asks you.",
    );
    expect(grantScopeLine("assistant_settings.auto_grant_until", "2099-01-01T00:00:00Z", { count: 0, hosts: [], no_host: 0 }))
      .toBe("Your queue is empty, so nothing would be sent on its own.");
    expect(grantScopeLine("assistant_settings.auto_grant_count", null, queue)).toBe("");
    expect(grantScopeLine("assistant_settings.daily_cap", 5, queue)).toBe("");
  });
});
