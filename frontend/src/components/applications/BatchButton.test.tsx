import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { BatchButton } from "./BatchButton";

const recordApplicationEvent = vi.fn();
vi.mock("@/lib/api", () => ({ recordApplicationEvent: (...a: unknown[]) => recordApplicationEvent(...a) }));
vi.mock("sonner", () => ({ toast: { error: vi.fn() } }));

beforeEach(() => {
  recordApplicationEvent.mockReset().mockResolvedValue({});
});

describe("BatchButton", () => {
  it("'Add to batch' records queued", async () => {
    const onRecorded = vi.fn();
    render(<BatchButton applicationId={4} queued={false} onRecorded={onRecorded} />);
    fireEvent.click(screen.getByTestId("batch-add"));
    await waitFor(() => expect(onRecorded).toHaveBeenCalled());
    expect(recordApplicationEvent).toHaveBeenCalledWith(4, { event_type: "queued" });
  });

  it("'Remove from batch' records unqueued", async () => {
    const onRecorded = vi.fn();
    render(<BatchButton applicationId={4} queued onRecorded={onRecorded} />);
    expect(screen.getByTestId("batch-remove")).toHaveTextContent("Remove from batch");
    fireEvent.click(screen.getByTestId("batch-remove"));
    await waitFor(() => expect(onRecorded).toHaveBeenCalled());
    expect(recordApplicationEvent).toHaveBeenCalledWith(4, { event_type: "unqueued", payload: { reason: "user_removed" } });
  });
});
