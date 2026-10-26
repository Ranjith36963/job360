import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AssistantInstructions } from "@/components/connect/AssistantInstructions";

const api = vi.hoisted(() => ({
  getProfile: vi.fn(),
  updateProfileFields: vi.fn(),
  keepProfileEdit: vi.fn(),
  takeBackProfileEdit: vi.fn(),
  getProfileEditHistory: vi.fn(),
}));
vi.mock("@/lib/api", () => api);
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

const NOW = new Date().toISOString();
const NOTES = "preferences.assistant_notes";

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  api.getProfile.mockResolvedValue({
    preferences: { assistant_notes: ["Never apply to agencies"], avoid_companies: ["Evil Corp"] },
    agent_edits: [],
  });
  api.getProfileEditHistory.mockResolvedValue([]);
});

describe("AssistantInstructions (Connect your assistant > Assistant configure)", () => {
  it("shows 'Things your AI assistant should know' ABOVE the sentence, saving the whole preferences block", async () => {
    api.updateProfileFields.mockResolvedValue({
      preferences: { assistant_notes: ["Never apply to agencies", "No night shifts"] },
      agent_edits: [],
    });
    render(<AssistantInstructions />);
    const card = await screen.findByTestId("assistant-notes-card");
    expect(card).toHaveTextContent("Things your AI assistant should know");
    expect(card).toHaveTextContent("Set instructions for your AI assistant");
    expect(card).toHaveTextContent("Never apply to agencies");

    fireEvent.change(screen.getByLabelText("New note for your assistant"), { target: { value: "No night shifts" } });
    fireEvent.click(screen.getByRole("button", { name: "Add note" }));
    await waitFor(() => expect(api.updateProfileFields).toHaveBeenCalledTimes(1));
    // One path only: the notes save can never overwrite the never-apply list.
    expect(api.updateProfileFields).toHaveBeenCalledWith([
      { path: NOTES, value: ["Never apply to agencies", "No night shifts"] },
    ]);
  });

  it("Take back waits for a note save still in flight, so that save cannot undo it", async () => {
    const agentEdit = {
      path: NOTES, value: ["Never apply to agencies"], previous_value: [], set_by: "agent:Claude", set_at: NOW,
    };
    api.getProfile.mockResolvedValue({
      preferences: { assistant_notes: ["Never apply to agencies"] },
      agent_edits: [agentEdit],
    });
    let finishSave: (p: unknown) => void = () => {};
    api.updateProfileFields.mockImplementation(() => new Promise((resolve) => (finishSave = resolve)));
    api.takeBackProfileEdit.mockResolvedValue({ preferences: { assistant_notes: [] }, agent_edits: [] });

    render(<AssistantInstructions />);
    await screen.findByTestId("assistant-notes-card");
    fireEvent.change(screen.getByLabelText("New note for your assistant"), { target: { value: "No night shifts" } });
    fireEvent.click(screen.getByRole("button", { name: "Add note" }));
    await waitFor(() => expect(api.updateProfileFields).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByTestId("take-back"));
    await new Promise((r) => setTimeout(r, 0));
    expect(api.takeBackProfileEdit).not.toHaveBeenCalled();
    expect(screen.getByLabelText("New note for your assistant")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Remove note: No night shifts" })).toBeDisabled();

    finishSave({ preferences: { assistant_notes: ["Never apply to agencies", "No night shifts"] }, agent_edits: [agentEdit] });
    await waitFor(() => expect(api.takeBackProfileEdit).toHaveBeenCalledWith(NOTES));
    await waitFor(() => expect(screen.getByTestId("assistant-notes-card")).not.toHaveTextContent("Never apply to agencies"));
    expect(api.updateProfileFields).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText("New note for your assistant")).toBeEnabled();
  });

  it("a failed Take back unlocks the notes again", async () => {
    api.getProfile.mockResolvedValue({
      preferences: { assistant_notes: ["Never apply to agencies"] },
      agent_edits: [{ path: NOTES, value: ["Never apply to agencies"], previous_value: [], set_by: "agent:Claude", set_at: NOW }],
    });
    api.takeBackProfileEdit.mockRejectedValue(new Error("500"));
    render(<AssistantInstructions />);
    await screen.findByTestId("assistant-notes-card");
    fireEvent.click(screen.getByTestId("take-back"));
    await waitFor(() => expect(api.takeBackProfileEdit).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByLabelText("New note for your assistant")).toBeEnabled());
    expect(screen.getByTestId("assistant-notes-card")).toHaveTextContent("Never apply to agencies");
  });

  it("no profile yet: the notes card is simply absent", async () => {
    api.getProfile.mockRejectedValue(new Error("404"));
    render(<AssistantInstructions />);
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByTestId("assistant-notes-card")).toBeNull();
    expect(screen.queryByTestId("never-apply-card")).toBeNull();
  });

  it("Never apply to lists the saved companies and saves one per line as that one preference", async () => {
    api.updateProfileFields.mockResolvedValue({ preferences: { avoid_companies: ["Evil Corp", "Acme"] }, agent_edits: [] });
    render(<AssistantInstructions />);
    const input = (await screen.findByTestId("never-apply-input")) as HTMLTextAreaElement;
    expect(input.value).toBe("Evil Corp");
    expect(screen.getByTestId("never-apply-save")).toBeDisabled();
    fireEvent.change(input, { target: { value: "Evil Corp\n  Acme \n\n" } });
    fireEvent.click(screen.getByTestId("never-apply-save"));
    await waitFor(() =>
      expect(api.updateProfileFields).toHaveBeenCalledWith([
        { path: "preferences.avoid_companies", value: ["Evil Corp", "Acme"] },
      ]),
    );
  });

  it("shows only the general instructions: Memory step facts stay in their step and ride along unchanged on every save", async () => {
    api.getProfile.mockResolvedValue({
      preferences: { assistant_notes: ["Never apply to agencies", "About you: I go by Alex.", "Logistics: No travel in August."] },
      agent_edits: [],
    });
    api.getProfileEditHistory.mockResolvedValue([
      { set_by: "web", set_at: NOW, value: ["Old general line", "Equality: Ask me first."] },
    ]);
    api.updateProfileFields.mockResolvedValue({ preferences: { assistant_notes: [] }, agent_edits: [] });
    render(<AssistantInstructions />);
    const card = await screen.findByTestId("assistant-notes-card");
    expect(card).toHaveTextContent("Never apply to agencies");
    expect(card).not.toHaveTextContent("I go by Alex");
    expect(card).not.toHaveTextContent("No travel in August");

    fireEvent.change(screen.getByLabelText("New note for your assistant"), { target: { value: "No night shifts" } });
    fireEvent.click(screen.getByRole("button", { name: "Add note" }));
    await waitFor(() => expect(api.updateProfileFields).toHaveBeenCalledTimes(1));
    expect(api.updateProfileFields).toHaveBeenCalledWith([
      { path: NOTES, value: ["Never apply to agencies", "No night shifts", "About you: I go by Alex.", "Logistics: No travel in August."] },
    ]);
  });
});

