import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { PersonToReach } from "@/lib/job-cards";
import { PersonRow } from "./PersonRow";

const updateContact = vi.fn();
vi.mock("@/lib/api", () => ({ updateContact: (...a: unknown[]) => updateContact(...a) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function person(contact: Record<string, unknown>, message = "Hi Ada"): PersonToReach {
  return {
    contact: { id: 4, name: "Ada", role: "CTO", found_via: null, ...contact },
    message,
    linkedinUrl: "https://www.linkedin.com/in/ada",
  } as unknown as PersonToReach;
}
const onChanged = vi.fn(async () => {});
function show(p: PersonToReach) {
  return render(<PersonRow person={p} busy={false} onSent={vi.fn()} onSkip={vi.fn()} onChanged={onChanged} />);
}

beforeEach(() => {
  updateContact.mockReset().mockResolvedValue({});
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
    show(person({ linkedin_state: "not_connected", ready_to_send: false }, "x".repeat(201)));
    expect(screen.getByTestId("note-count-4")).toHaveTextContent("201/200");
    expect(screen.getByTestId("note-count-4")).toHaveAttribute("data-over", "true");
    const send = screen.getByTestId("send-browser-4");
    expect(send).toBeDisabled();
    expect(send).toHaveAttribute("title", expect.stringContaining("200"));
  });

  it("shows no counter for a connected person, only a Message label", () => {
    show(person({ linkedin_state: "connected" }, "x".repeat(500)));
    expect(screen.queryByTestId("note-count-4")).toBeNull();
    expect(screen.getByText("Message")).toBeInTheDocument();
  });

  it("has no send button without the ready_to_send key", () => {
    show(person({ linkedin_state: "connected" }));
    expect(screen.queryByText("Send with my assistant's browser")).toBeNull();
  });

  it("queues a send when ready_to_send is false", async () => {
    show(person({ linkedin_state: "connected", ready_to_send: false }));
    fireEvent.click(screen.getByText("Send with my assistant's browser"));
    await waitFor(() => expect(updateContact).toHaveBeenCalledWith(4, { ready_to_send: true }));
    expect(onChanged).toHaveBeenCalled();
  });

  it("shows a disabled queued state when ready_to_send is true", () => {
    show(person({ linkedin_state: "connected", ready_to_send: true }));
    expect(screen.getByText("Queued for your assistant's browser")).toBeDisabled();
    expect(screen.queryByText("Send with my assistant's browser")).toBeNull();
  });
});
