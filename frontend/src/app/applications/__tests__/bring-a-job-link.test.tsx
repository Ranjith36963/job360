/**
 * Owner report, 2026-09-25: the "Bring a job" button that sits in the
 * heading row on the signed-in home (`src/app/page.tsx`) was missing from
 * `/applications` itself, even though both pages render the exact same
 * `ApplicationList`. This guards that `/applications` carries a link to
 * `/bring`, top-right next to the heading, the same way the home page does.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import ApplicationsPage from "../page";

vi.mock("@/lib/api", () => ({
  listApplications: vi.fn().mockResolvedValue({ applications: [], total: 0 }),
}));

describe("/applications — Bring a job button (owner report, 2026-09-25)", () => {
  it("renders a Bring a job link to /bring, top-right next to the heading", async () => {
    render(<ApplicationsPage />);
    const heading = await screen.findByText("Your applications");

    // Scoped to the header row (the heading's parent's parent), same shape
    // as src/app/page.tsx — not the *All*By query, because the empty-state
    // copy below also links to /bring and isn't the button being tested.
    const headerRow = heading.parentElement?.parentElement as HTMLElement;
    const link = within(headerRow).getByRole("link", { name: "Bring a job" });
    expect(link).toHaveAttribute("href", "/bring");
  });
});
