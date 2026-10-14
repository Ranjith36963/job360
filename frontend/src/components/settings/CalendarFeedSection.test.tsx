import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CalendarFeedSection } from "./CalendarFeedSection";

const getCalendarFeed = vi.fn();
const createCalendarFeed = vi.fn();
const revokeCalendarFeed = vi.fn();

vi.mock("@/lib/api", () => ({
  getCalendarFeed: (...a: unknown[]) => getCalendarFeed(...a),
  createCalendarFeed: (...a: unknown[]) => createCalendarFeed(...a),
  revokeCalendarFeed: (...a: unknown[]) => revokeCalendarFeed(...a),
}));

const URL_ = "https://job360.uk/api/calendar/abc123.ics";

describe("CalendarFeedSection", () => {
  beforeEach(() => {
    getCalendarFeed.mockReset().mockResolvedValue({ exists: false, created_at: null });
    createCalendarFeed.mockReset().mockResolvedValue({ url: URL_ });
    revokeCalendarFeed.mockReset().mockResolvedValue(undefined);
  });

  it("creates the feed and shows the URL once, masked from session replay", async () => {
    const user = userEvent.setup();
    render(<CalendarFeedSection />);
    expect(screen.queryByTestId("calendar-feed-url")).toBeNull();
    expect(screen.queryByTestId("calendar-feed-revoke")).toBeNull();

    const create = screen.getByTestId("calendar-feed-create");
    await waitFor(() => expect(create).not.toBeDisabled());
    await user.click(create);

    const shown = await screen.findByTestId("calendar-feed-url");
    expect(shown).toHaveTextContent(URL_);
    expect(shown).toHaveAttribute("data-sentry-mask");
    expect(screen.getByTestId("calendar-feed-copy")).toBeInTheDocument();
    expect(screen.getByTestId("calendar-feed-revoke")).toBeInTheDocument();
  });

  it("copies the URL to the clipboard", async () => {
    const user = userEvent.setup();
    render(<CalendarFeedSection />);
    const create = screen.getByTestId("calendar-feed-create");
    await waitFor(() => expect(create).not.toBeDisabled());
    await user.click(create);
    await screen.findByTestId("calendar-feed-url");
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue(undefined);
    await user.click(screen.getByTestId("calendar-feed-copy"));
    expect(writeText).toHaveBeenCalledWith(URL_);
  });

  it("an existing feed never re-shows its URL, and Revoke clears it", async () => {
    getCalendarFeed.mockResolvedValue({ exists: true, created_at: "2026-10-14T00:00:00+00:00" });
    const user = userEvent.setup();
    render(<CalendarFeedSection />);
    const revoke = await screen.findByTestId("calendar-feed-revoke");
    expect(screen.queryByTestId("calendar-feed-url")).toBeNull();
    expect(screen.getByTestId("calendar-feed-create")).toHaveTextContent(/regenerate/i);
    await user.click(revoke);
    await waitFor(() => expect(revokeCalendarFeed).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByTestId("calendar-feed-revoke")).toBeNull());
  });
});
