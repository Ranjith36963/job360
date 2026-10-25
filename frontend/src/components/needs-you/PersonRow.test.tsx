import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { PersonToReach } from "@/lib/job-cards";
import { PersonRow } from "./PersonRow";

const requestJson = vi.fn();
vi.mock("@/lib/api", () => ({ requestJson: (...a: unknown[]) => requestJson(...a) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function person(contact: Record<string, unknown>, message = "Hi Ada"): PersonToReach {
  return {
    contact: { id: 4, name: "Ada", role: "CTO", found_via: null, ...contact },
    message,
    linkedinUrl: "https://www.linkedin.com/in/ada",
  } as unknown as PersonToReach;
}
const task = (state: string, message_id = 5) => ({ state, message_id, approved_at: null, claim_until: null });
const onChanged = vi.fn(async () => {});
function show(p: PersonToReach) {
  return render(<PersonRow person={p} busy={false} onSent={vi.fn()} onSkip={vi.fn()} onChanged={onChanged} />);
}

beforeEach(() => {
  requestJson.mockReset().mockResolvedValue({});
  onChanged.mockClear();
});

describe("PersonRow", () => {
  it.each([
    ["connected", "Connected"],
    ["not_connected", "Not connected"],
    ["pending", "Request pending"],
    ["unknown", "LinkedIn state unknown"],
    [null, "LinkedIn state unknown"],
  ])("labels state %s", (state, label) => {
    show(person({ linkedin_state: state }));
    expect(screen.getByTestId("li-state-4")).toHaveTextContent(label);
  });

  it("counts a connection note and warns over 200", () => {
    const { unmount } = show(person({ linkedin_state: "not_connected" }, "x".repeat(150)));
    expect(screen.getByTestId("note-count-4")).toHaveTextContent("150/200");
    expect(screen.getByTestId("note-count-4")).toHaveAttribute("data-over", "false");
    expect(screen.getByText("LinkedIn connection notes are 200 characters at most")).toBeInTheDocument();
    unmount();
    show(person({ linkedin_state: "not_connected", own_browser_task: task("waiting_on_you") }, "x".repeat(201)));
    expect(screen.getByTestId("note-count-4")).toHaveTextContent("201/200");
    expect(screen.getByTestId("note-count-4")).toHaveAttribute("data-over", "true");
    expect(screen.getByTestId("note-count-4")).toHaveClass("text-danger");
    // Hidden, not just disabled: nothing to press that would send a too-long note.
    expect(screen.queryByTestId("send-browser-4")).toBeNull();
    expect(screen.queryByRole("button", { name: "Copy" })).toBeNull();
    expect(screen.getByTestId("note-too-long-4")).toHaveTextContent("Too long for a connection note. Use it as a message after you connect, or rewrite it.");
    expect(screen.getByText("Open profile ↗")).toBeInTheDocument();
    expect(screen.getByTestId("sent-4")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Skip" })).toBeInTheDocument();
    expect(screen.getByTestId("decline-browser-4")).toBeInTheDocument();
  });

  it("keeps Copy and the browser button at exactly 200 characters", () => {
    show(person({ linkedin_state: "not_connected", own_browser_task: task("waiting_on_you") }, "x".repeat(200)));
    expect(screen.getByTestId("send-browser-4")).toBeEnabled();
    expect(screen.getByRole("button", { name: "Copy" })).toBeInTheDocument();
    expect(screen.queryByTestId("note-too-long-4")).toBeNull();
  });

  it("never limits a connected contact's message", () => {
    show(person({ linkedin_state: "connected", own_browser_task: task("waiting_on_you") }, "x".repeat(500)));
    expect(screen.queryByTestId("note-too-long-4")).toBeNull();
    expect(screen.getByTestId("send-browser-4")).toBeEnabled();
    expect(screen.getByRole("button", { name: "Copy" })).toBeInTheDocument();
  });

  it("shows no counter for a connected person, only a Message label", () => {
    show(person({ linkedin_state: "connected" }, "x".repeat(500)));
    expect(screen.queryByTestId("note-count-4")).toBeNull();
    expect(screen.getByText("Message")).toBeInTheDocument();
  });

  it("has no browser button without own_browser_task", () => {
    show(person({ linkedin_state: "connected" }));
    expect(screen.queryByText("Use my assistant's browser")).toBeNull();
    expect(screen.queryByText("Don't use it")).toBeNull();
  });

  it("approves with the task's message_id", async () => {
    show(person({ linkedin_state: "connected", own_browser_task: task("waiting_on_you", 77) }));
    fireEvent.click(screen.getByText("Use my assistant's browser"));
    await waitFor(() =>
      expect(requestJson).toHaveBeenCalledWith("/api/contacts/4/own-browser-send", "POST", { message_id: 77, decision: "approve" }));
    expect(onChanged).toHaveBeenCalled();
  });

  it("declines with a quiet button", async () => {
    show(person({ linkedin_state: "connected", own_browser_task: task("waiting_on_you", 77) }));
    fireEvent.click(screen.getByText("Don't use it"));
    await waitFor(() =>
      expect(requestJson).toHaveBeenCalledWith("/api/contacts/4/own-browser-send", "POST", { message_id: 77, decision: "decline" }));
  });

  it("shows queued, in-progress and declined states without an approve button", () => {
    const { unmount } = show(person({ linkedin_state: "connected", own_browser_task: task("queued") }));
    expect(screen.getByText("Queued for your browser")).toBeDisabled();
    expect(screen.queryByText("Use my assistant's browser")).toBeNull();
    unmount();
    const r2 = show(person({ linkedin_state: "connected", own_browser_task: task("in_progress") }));
    expect(screen.getByText("Sending in your browser…")).toBeDisabled();
    r2.unmount();
    show(person({ linkedin_state: "connected", own_browser_task: task("declined") }));
    expect(screen.getByText("You said not to use your browser")).toBeInTheDocument();
    expect(screen.queryByText("Use my assistant's browser")).toBeNull();
  });

  it("ignores a malformed own_browser_task", () => {
    show(person({ linkedin_state: "connected", own_browser_task: { state: "weird", message_id: 1 } }));
    expect(screen.queryByText("Use my assistant's browser")).toBeNull();
  });

  it("shows the send browser label, and its reason only for own_browser or human", () => {
    const { unmount } = show(person({ linkedin_state: "connected",
      send_browser: { kind: "own_browser", label: "Your Chrome", reason: "Job360's browser failed twice" } }));
    expect(screen.getByTestId("send-browser-label-4")).toHaveTextContent("Your Chrome");
    expect(screen.getByTestId("send-browser-reason-4")).toHaveTextContent("Job360's browser failed twice");
    unmount();
    show(person({ linkedin_state: "connected", send_browser: { kind: "job360", label: "Job360's browser", reason: "first choice" } }));
    expect(screen.getByTestId("send-browser-label-4")).toHaveTextContent("Job360's browser");
    expect(screen.queryByTestId("send-browser-reason-4")).toBeNull();
  });

  it("shows LinkedIn as the channel when the contact has no email", () => {
    show(person({ linkedin_state: "connected", email: "" }));
    expect(screen.getByTestId("channel-4")).toHaveTextContent("LinkedIn");
    expect(screen.queryByTestId("email-4")).toBeNull();
  });

  it("shows Email as the channel, the address and where it came from", () => {
    show(person({ linkedin_state: "connected", email: "ada@acme.com", email_source: "hunter" }));
    expect(screen.getByTestId("channel-4")).toHaveTextContent("Email");
    expect(screen.getByTestId("email-4")).toHaveTextContent("ada@acme.com · from Hunter");
  });
});
