import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { OtherList } from "./OtherList";

const listInboxItems = vi.fn();
const resolveInboxItem = vi.fn();
const listApplications = vi.fn();
vi.mock("@/lib/api", () => ({
  listInboxItems: (...a: unknown[]) => listInboxItems(...a),
  resolveInboxItem: (...a: unknown[]) => resolveInboxItem(...a),
  listApplications: (...a: unknown[]) => listApplications(...a),
  listOutcomeSuggestions: async () => ({ suggestions: [], truncated: false }),
}));

// Unmatched mail now lives as rows of the one Other list (owner plan 3b): Match opens the card inline.
const UnmatchedMail = () => <OtherList waiting={[]} questions={[]} onChanged={async () => {}} renderQuestion={() => null} />;
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const item = {
  id: 5, kind: "email", message_id: "u-1", thread_id: "thr-globex-9", sender: "talent@globex.example",
  subject: "Your application at Globex", received_at: "2026-10-18T09:30:00+00:00", recorded_by: "token:cc",
  created_at: "2026-10-18T09:31:00+00:00", resolution: "", application_id: null, job_title: null,
  job_company: null, resolved_by: null, resolved_at: null, event_id: null,
};
const apps = {
  applications: [
    { id: 1, job_title: "Data Engineer", job_company: "Northwind" },
    { id: 2, job_title: "Platform Engineer", job_company: "Contoso" },
  ],
};

describe("UnmatchedMail", () => {
  beforeEach(() => {
    listInboxItems.mockReset();
    resolveInboxItem.mockReset().mockResolvedValue(item);
    listApplications.mockReset().mockResolvedValue(apps);
  });

  it("shows the item, enables Match only after a pick, resolves, and dismisses", async () => {
    listInboxItems.mockResolvedValue({ items: [item], open_count: 1 });
    render(<UnmatchedMail />);
    expect(await screen.findByTestId("other-open-mail-5")).toHaveTextContent("Match");
    fireEvent.click(screen.getByTestId("other-open-mail-5"));
    const row = await screen.findByTestId("inbox-item-5");
    expect(row).toHaveTextContent("talent@globex.example");
    expect(row).toHaveTextContent("Your application at Globex");
    const match = screen.getByTestId("inbox-item-match-5");
    expect(match).toBeDisabled();
    await screen.findByText("Platform Engineer · Contoso");
    fireEvent.change(screen.getByTestId("inbox-item-app-5"), { target: { value: "2" } });
    fireEvent.click(match);
    await waitFor(() => expect(resolveInboxItem).toHaveBeenCalledWith(5, { application_id: 2 }));
    fireEvent.click(screen.getByTestId("inbox-item-dismiss-5"));
    await waitFor(() => expect(resolveInboxItem).toHaveBeenCalledWith(5, { dismiss: true }));
  });

  it("renders nothing with no items or a failed read", async () => {
    listInboxItems.mockResolvedValue({ items: [], open_count: 0 });
    const { container, rerender } = render(<UnmatchedMail />);
    await waitFor(() => expect(listInboxItems).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
    listInboxItems.mockRejectedValue(new Error("down"));
    rerender(<UnmatchedMail key="again" />);
    await waitFor(() => expect(listInboxItems).toHaveBeenCalledTimes(2));
    expect(container).toBeEmptyDOMElement();
  });
});
