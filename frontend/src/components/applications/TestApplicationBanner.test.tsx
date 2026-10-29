import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { TestApplicationBanner } from "./TestApplicationBanner";

const recordApplicationEvent = vi.fn();
vi.mock("@/lib/api", () => ({
  recordApplicationEvent: (...a: unknown[]) => recordApplicationEvent(...a),
}));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

beforeEach(() => {
  recordApplicationEvent.mockReset().mockResolvedValue({});
});

describe("TestApplicationBanner", () => {
  it("says it is a test application in plain words", () => {
    render(<TestApplicationBanner applicationId={7} markEventId={41} onChanged={vi.fn()} />);
    expect(screen.getByTestId("test-application-banner")).toHaveTextContent(
      "Test application. It is left out of your lists and Needs you.",
    );
  });

  it("'Not a test? Undo' records a note correcting the mark, then refreshes", async () => {
    const onChanged = vi.fn();
    render(<TestApplicationBanner applicationId={7} markEventId={41} onChanged={onChanged} />);
    fireEvent.click(screen.getByTestId("test-application-undo"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(recordApplicationEvent).toHaveBeenCalledWith(7, {
      event_type: "note",
      detail: "Not a test application.",
      corrects_event_id: 41,
    });
  });

  it("shows no Undo without a mark to correct", () => {
    render(<TestApplicationBanner applicationId={7} markEventId={null} onChanged={vi.fn()} />);
    expect(screen.queryByTestId("test-application-undo")).toBeNull();
  });
});
