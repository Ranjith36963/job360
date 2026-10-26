import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { FormHandoff } from "./FormHandoff";

const refillForm = vi.fn();
vi.mock("@/lib/api", () => ({ refillForm: (...a: unknown[]) => refillForm(...a), browserLive: vi.fn() }));
vi.mock("./FinishInBrowser", () => ({
  FinishInBrowser: ({ startMaximized }: { startMaximized?: boolean }) => (
    <div data-testid="finish-browser" data-maximized={String(!!startMaximized)} />
  ),
}));
vi.mock("@/lib/toast", () => ({ toast: { error: vi.fn(), apiError: vi.fn() } }));

const URL_ = "https://ats.rippling.com/acme/jobs/1";
beforeEach(() => refillForm.mockReset());

describe("FormHandoff", () => {
  it("finish_here: Finish it here comes first, the raw link is the demoted empty-form link", () => {
    render(<FormHandoff applicationId={7} finishHere formUrl={URL_} heldSeconds={600} />);
    const finish = screen.getByTestId("finish-here");
    const link = screen.getByTestId("ask-open-empty-form");
    expect(finish).toHaveTextContent("Finish it here");
    expect(link).toHaveTextContent("Open the empty form in my own browser instead");
    expect(link).toHaveAttribute("href", URL_);
    expect(finish.compareDocumentPosition(link) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByTestId("ask-form-closed")).toBeNull();
    fireEvent.click(finish);
    expect(screen.getByTestId("finish-browser")).toHaveAttribute("data-maximized", "false"); // a card
  });

  it("not finish_here and no can_refill: says the form closed, no Refill button", () => {
    render(<FormHandoff applicationId={7} finishHere={false} formUrl={URL_} />);
    expect(screen.getByTestId("ask-form-closed")).toHaveTextContent(
      "The filled form was closed (Job360 restarted or 30 minutes passed).",
    );
    expect(screen.queryByTestId("ask-refill")).toBeNull();
    expect(screen.queryByTestId("finish-here")).toBeNull();
  });

  it("can_refill: shows progress, then opens FinishInBrowser on ok", async () => {
    let done: (v: unknown) => void = () => {};
    refillForm.mockReturnValue(new Promise((r) => (done = r)));
    render(<FormHandoff applicationId={7} finishHere={false} canRefill />);
    fireEvent.click(screen.getByTestId("ask-refill"));
    expect(screen.getByTestId("ask-refill-progress")).toHaveTextContent("Refilling your form… (about a minute)");
    expect(refillForm).toHaveBeenCalledWith(7);
    done({ status: "ok" });
    await waitFor(() => expect(screen.getByTestId("finish-browser")).toBeInTheDocument());
    expect(screen.queryByTestId("ask-refill-progress")).toBeNull();
    expect(screen.getByTestId("finish-browser")).toHaveAttribute("data-maximized", "true"); // lands full-screen
  });

  it("refused: shows the detail and no live view", async () => {
    refillForm.mockResolvedValue({ status: "refused", reason: "busy", detail: "Another form is open." });
    render(<FormHandoff applicationId={7} finishHere={false} canRefill />);
    fireEvent.click(screen.getByTestId("ask-refill"));
    await waitFor(() => expect(screen.getByTestId("ask-refill-refused")).toHaveTextContent("Another form is open."));
    expect(screen.queryByTestId("finish-browser")).toBeNull();
  });
});
