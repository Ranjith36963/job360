import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { LessonRow } from "./LessonRow";

const recordApplicationEvent = vi.fn();
vi.mock("@/lib/api", () => ({
  recordApplicationEvent: (...args: unknown[]) => recordApplicationEvent(...args),
}));

function setup(recordedBy = "token:claude") {
  const onChanged = vi.fn().mockResolvedValue(undefined);
  render(
    <LessonRow applicationId={42} eventId={7} detail="Mention the cert." recordedBy={recordedBy} onChanged={onChanged} />
  );
  return onChanged;
}

describe("LessonRow", () => {
  beforeEach(() => {
    recordApplicationEvent.mockReset();
    recordApplicationEvent.mockResolvedValue({ event_id: 8 });
  });

  it("edit records a new lesson event that corrects the old one", async () => {
    const onChanged = setup();
    fireEvent.click(screen.getByTestId("lesson-edit-7"));
    fireEvent.change(screen.getByTestId("lesson-input-7"), { target: { value: "  Mention the CKA cert.  " } });
    fireEvent.click(screen.getByTestId("lesson-save-7"));
    await waitFor(() =>
      expect(recordApplicationEvent).toHaveBeenCalledWith(42, {
        event_type: "lesson",
        detail: "Mention the CKA cert.",
        corrects_event_id: 7,
      })
    );
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("cancel records nothing", () => {
    setup();
    fireEvent.click(screen.getByTestId("lesson-edit-7"));
    fireEvent.click(screen.getByTestId("lesson-cancel-7"));
    expect(recordApplicationEvent).not.toHaveBeenCalled();
    expect(screen.getByTestId("lesson-edit-7")).toBeInTheDocument();
  });

  it("take back asks in the page first, then records a correcting note", async () => {
    const onChanged = setup();
    fireEvent.click(screen.getByTestId("lesson-takeback-7"));
    expect(recordApplicationEvent).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("lesson-takeback-confirm-7"));
    await waitFor(() =>
      expect(recordApplicationEvent).toHaveBeenCalledWith(42, {
        event_type: "note",
        detail: "Lesson taken back.",
        corrects_event_id: 7,
      })
    );
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("shows who wrote it", () => {
    setup("web");
    expect(screen.getByTestId("lesson-author-7")).toHaveTextContent("Written by you");
  });

  it("labels an agent author as AI", () => {
    setup("token:claude");
    expect(screen.getByTestId("lesson-author-7")).toHaveTextContent("Written by AI");
  });
});
