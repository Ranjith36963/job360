import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { FollowUpField } from "./FollowUpField";

const recordApplicationEvent = vi.fn();

vi.mock("@/lib/api", () => ({
  recordApplicationEvent: (...args: unknown[]) => recordApplicationEvent(...args),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

describe("FollowUpField", () => {
  beforeEach(() => {
    recordApplicationEvent.mockReset();
    recordApplicationEvent.mockResolvedValue({});
  });

  it("Save sends a note event carrying follow_up_on", async () => {
    const onRecorded = vi.fn().mockResolvedValue(undefined);
    render(<FollowUpField applicationId={7} followUpOn={null} onRecorded={onRecorded} />);

    fireEvent.change(screen.getByTestId("follow-up-date"), { target: { value: "2026-10-03" } });
    fireEvent.click(screen.getByTestId("follow-up-save"));

    await waitFor(() =>
      expect(recordApplicationEvent).toHaveBeenCalledWith(
        7,
        expect.objectContaining({ event_type: "note", follow_up_on: "2026-10-03" })
      )
    );
    expect(onRecorded).toHaveBeenCalled();
  });

  it("Clear sends follow_up_on as an empty string", async () => {
    const onRecorded = vi.fn().mockResolvedValue(undefined);
    render(<FollowUpField applicationId={7} followUpOn="2026-10-03" onRecorded={onRecorded} />);

    fireEvent.click(screen.getByTestId("follow-up-clear"));

    await waitFor(() =>
      expect(recordApplicationEvent).toHaveBeenCalledWith(
        7,
        expect.objectContaining({ event_type: "note", follow_up_on: "" })
      )
    );
  });

  it("does not render Clear when there is no saved date", () => {
    render(<FollowUpField applicationId={7} followUpOn={null} onRecorded={vi.fn()} />);
    expect(screen.queryByTestId("follow-up-clear")).toBeNull();
  });

  it("Save is disabled until the date changes", () => {
    render(<FollowUpField applicationId={7} followUpOn="2026-10-03" onRecorded={vi.fn()} />);
    expect(screen.getByTestId("follow-up-save")).toBeDisabled();

    fireEvent.change(screen.getByTestId("follow-up-date"), { target: { value: "2026-10-04" } });
    expect(screen.getByTestId("follow-up-save")).not.toBeDisabled();
  });
});
