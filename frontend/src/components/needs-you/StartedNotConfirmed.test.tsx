import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { StartedNotConfirmed, startedNotConfirmed } from "./StartedNotConfirmed";

const fetchWhatsNewPages = vi.fn();
vi.mock("@/lib/home-fetch", () => ({ fetchWhatsNewPages: (...a: unknown[]) => fetchWhatsNewPages(...a) }));

const ev = (id: number, application_id: number, event_type: string) => ({
  id, application_id, event_type, occurred_at: "2026-10-17T10:00:00Z", payload: {}, corrects_event_id: null,
});
const apps = [
  { id: 1, job_title: "Engineer", job_company: "Acme" },
  { id: 2, job_title: "Analyst", job_company: "Beta" },
];

describe("startedNotConfirmed", () => {
  it("keeps only applications whose newest submit event is 'started'", () => {
    const rows = startedNotConfirmed(
      [ev(1, 1, "submit_attempted"), ev(2, 2, "submit_attempted"), ev(3, 2, "applied")],
      apps,
    );
    expect(rows.map((r) => r.id)).toEqual([1]);
    expect(rows[0]).toMatchObject({ title: "Engineer", company: "Acme" });
  });
});

describe("StartedNotConfirmed", () => {
  beforeEach(() => {
    fetchWhatsNewPages.mockReset();
  });

  it("lists them under 'Started, not confirmed'", async () => {
    fetchWhatsNewPages.mockResolvedValue({ events: [ev(1, 1, "submit_attempted")], applications: apps });
    render(<StartedNotConfirmed />);
    const row = await screen.findByTestId("started-1");
    expect(row).toHaveTextContent("Engineer · Acme");
    expect(screen.getByTestId("started-not-confirmed")).toHaveTextContent("Started, not confirmed");
  });

  it("shows nothing when there are none", async () => {
    fetchWhatsNewPages.mockResolvedValue({ events: [], applications: [] });
    const { container } = render(<StartedNotConfirmed />);
    await waitFor(() => expect(fetchWhatsNewPages).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("shows nothing when the feed was cut short (the confirming event may be missing)", async () => {
    fetchWhatsNewPages.mockResolvedValue({ events: [ev(1, 1, "submit_attempted")], applications: apps, truncated: true });
    const { container } = render(<StartedNotConfirmed />);
    await waitFor(() => expect(fetchWhatsNewPages).toHaveBeenCalled());
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(container).toBeEmptyDOMElement();
  });

  it("shows nothing when the read fails", async () => {
    fetchWhatsNewPages.mockRejectedValue(new Error("down"));
    const { container } = render(<StartedNotConfirmed />);
    await waitFor(() => expect(fetchWhatsNewPages).toHaveBeenCalled());
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(container).toBeEmptyDOMElement();
  });
});
