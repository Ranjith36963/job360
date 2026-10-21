import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SubmitUnconfirmedBanner } from "./SubmitUnconfirmedBanner";

const resolveSubmit = vi.fn();
const recordApplicationReceipt = vi.fn();
vi.mock("@/lib/api", () => ({
  resolveSubmit: (...a: unknown[]) => resolveSubmit(...a),
  recordApplicationReceipt: (...a: unknown[]) => recordApplicationReceipt(...a),
}));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

beforeEach(() => {
  resolveSubmit.mockReset().mockResolvedValue({});
  recordApplicationReceipt.mockReset().mockResolvedValue({});
});

describe("SubmitUnconfirmedBanner", () => {
  it("says what happened in plain words", () => {
    render(<SubmitUnconfirmedBanner applicationId={7} onChanged={vi.fn()} />);
    expect(screen.getByTestId("submit-unconfirmed")).toHaveTextContent(
      "Your assistant started submitting this and it was never confirmed. Check the employer's email or site.",
    );
  });

  it("'It was not sent' calls resolve, then refreshes", async () => {
    const onChanged = vi.fn();
    render(<SubmitUnconfirmedBanner applicationId={7} onChanged={onChanged} />);
    fireEvent.click(screen.getByTestId("submit-not-sent"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(resolveSubmit).toHaveBeenCalledWith(7);
    expect(recordApplicationReceipt).not.toHaveBeenCalled();
  });

  it("'It was sent' asks first, then records the application", async () => {
    const onChanged = vi.fn();
    render(<SubmitUnconfirmedBanner applicationId={7} onChanged={onChanged} />);
    fireEvent.click(screen.getByTestId("submit-was-sent"));
    expect(recordApplicationReceipt).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("submit-sent-confirm"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(recordApplicationReceipt).toHaveBeenCalledWith(7, {});
  });

  it("cancel goes back without recording", () => {
    render(<SubmitUnconfirmedBanner applicationId={7} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByTestId("submit-was-sent"));
    fireEvent.click(screen.getByTestId("submit-sent-cancel"));
    expect(screen.getByTestId("submit-was-sent")).toBeInTheDocument();
    expect(recordApplicationReceipt).not.toHaveBeenCalled();
  });
});
