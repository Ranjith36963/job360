import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { LessonForm } from "./LessonForm";

const recordApplicationEvent = vi.fn();
vi.mock("@/lib/api", () => ({
  recordApplicationEvent: (...args: unknown[]) => recordApplicationEvent(...args),
}));

describe("LessonForm", () => {
  beforeEach(() => recordApplicationEvent.mockReset());

  it("records a lesson event with the trimmed text and calls onRecorded", async () => {
    recordApplicationEvent.mockResolvedValue({
      event_id: 1,
      application_id: 42,
      event_type: "lesson",
      status: null,
      status_changed: false,
    });
    const onRecorded = vi.fn().mockResolvedValue(undefined);

    render(<LessonForm applicationId={42} onRecorded={onRecorded} />);

    fireEvent.change(screen.getByTestId("lesson-input"), {
      target: { value: "  Always mention the Kubernetes cert.  " },
    });
    fireEvent.click(screen.getByTestId("lesson-submit"));

    await waitFor(() =>
      expect(recordApplicationEvent).toHaveBeenCalledWith(42, {
        event_type: "lesson",
        detail: "Always mention the Kubernetes cert.",
      })
    );
    await waitFor(() => expect(onRecorded).toHaveBeenCalled());
  });

  it("disables the submit button when the input is empty", () => {
    render(<LessonForm applicationId={42} onRecorded={vi.fn()} />);
    expect(screen.getByTestId("lesson-submit")).toBeDisabled();
  });
});
