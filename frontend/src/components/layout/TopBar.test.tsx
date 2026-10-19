import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { PausedBanner } from "./PausedBanner";
import { TopBar } from "./TopBar";

const getAssistantSettings = vi.fn();
const updateProfileFields = vi.fn();
const toastError = vi.fn();

vi.mock("next/navigation", () => ({ usePathname: () => "/needs-you" }));
vi.mock("@/components/layout/AuthProvider", () => ({ useAuth: () => ({ user: { email: "a@b.co" } }) }));
vi.mock("@/lib/api", () => ({
  getAssistantSettings: (...a: unknown[]) => getAssistantSettings(...a),
  updateProfileFields: (...a: unknown[]) => updateProfileFields(...a),
}));
vi.mock("sonner", () => ({ toast: { error: (...a: unknown[]) => toastError(...a) } }));

const edit = (value: string) => [{ path: "assistant_settings.paused_until", value }];
const Frame = () => (
  <>
    <TopBar />
    <PausedBanner />
  </>
);
const startPaused = async () => {
  getAssistantSettings.mockResolvedValue({ paused: true, waiting: [] });
  render(<Frame />);
  await screen.findByTestId("paused-banner");
};

beforeEach(() => {
  getAssistantSettings.mockReset().mockResolvedValue({ paused: false, waiting: [] });
  updateProfileFields.mockReset().mockResolvedValue({});
  toastError.mockReset();
});

describe("pause button", () => {
  it("running: page name, 'Pause assistants', no banner", async () => {
    render(<Frame />);
    expect(screen.getByTestId("top-bar")).toHaveTextContent("Needs you");
    expect(screen.getByTestId("pause-button")).toHaveTextContent("Pause assistants");
    expect(screen.getByTestId("pause-button")).toHaveAttribute("aria-pressed", "false");
    await waitFor(() => expect(getAssistantSettings).toHaveBeenCalled());
    expect(screen.queryByTestId("paused-banner")).toBeNull();
  });

  it("one tap sends the exact edit; banner + amber 'Assistants paused' follow", async () => {
    render(<Frame />);
    fireEvent.click(screen.getByTestId("pause-button"));
    await screen.findByTestId("paused-banner");
    expect(updateProfileFields).toHaveBeenCalledTimes(1);
    expect(updateProfileFields).toHaveBeenCalledWith(edit("until_resumed"));
    const btn = screen.getByTestId("pause-button");
    expect(btn).toHaveTextContent("Assistants paused");
    expect(btn).toHaveAttribute("aria-pressed", "true");
    expect(btn.className).toContain("text-warning");
    expect(screen.queryByTestId("resume-confirm")).toBeNull();
  });

  it("a failed pause keeps the old state and says so", async () => {
    updateProfileFields.mockRejectedValue(new Error("down"));
    render(<Frame />);
    fireEvent.click(screen.getByTestId("pause-button"));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("Could not pause. Try again."));
    expect(screen.getByTestId("pause-button")).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByTestId("paused-banner")).toBeNull();
  });
});

describe("paused banner", () => {
  it("says what stops and what still works", async () => {
    await startPaused();
    expect(screen.getByTestId("paused-banner")).toHaveTextContent(
      "Paused. Your assistants can still read your memory, but won't apply or send anything.",
    );
  });

  it("Resume asks first; Stay paused changes nothing and calls nothing", async () => {
    await startPaused();
    fireEvent.click(screen.getByTestId("paused-banner-resume"));
    expect(screen.getByTestId("resume-confirm")).toHaveTextContent(
      "Resume? Your assistants will apply and send again under your settings.",
    );
    fireEvent.click(screen.getByTestId("resume-no"));
    expect(screen.queryByTestId("resume-confirm")).toBeNull();
    expect(screen.getByTestId("paused-banner")).toBeInTheDocument();
    expect(updateProfileFields).not.toHaveBeenCalled();
  });

  it("the amber top-bar button opens the same confirm; Resume sends '' and clears", async () => {
    await startPaused();
    fireEvent.click(screen.getByTestId("pause-button"));
    fireEvent.click(await screen.findByTestId("resume-yes"));
    await waitFor(() => expect(screen.queryByTestId("paused-banner")).toBeNull());
    expect(updateProfileFields).toHaveBeenCalledWith(edit(""));
    expect(screen.getByTestId("pause-button")).toHaveTextContent("Pause assistants");
  });

  it("a failed resume stays paused with the confirm open", async () => {
    await startPaused();
    updateProfileFields.mockRejectedValue(new Error("down"));
    fireEvent.click(screen.getByTestId("paused-banner-resume"));
    fireEvent.click(screen.getByTestId("resume-yes"));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("Could not resume. Try again."));
    expect(screen.getByTestId("paused-banner")).toBeInTheDocument();
    expect(screen.getByTestId("resume-confirm")).toBeInTheDocument();
  });
});
