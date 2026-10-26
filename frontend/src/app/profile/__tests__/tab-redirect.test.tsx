import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

const replace = vi.fn();
let search = "";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace }),
  useSearchParams: () => new URLSearchParams(search),
  usePathname: () => "/profile",
}));

import ProfilePage from "../page";
import { ProfileHeader } from "@/components/profile/ProfileHeader";

// The Profile tab row is gone (owner sketch, 2026-10-26): Memory is its own page under Profile and the
// assistant settings live on Connect your assistant. Old /profile?tab= links land there.
beforeEach(() => replace.mockClear());

function goesTo(qs: string): unknown {
  search = qs;
  render(<ProfilePage />);
  return replace.mock.calls.at(-1)?.[0];
}

describe("old /profile?tab= links", () => {
  // The three old tabs keep a home: CV is /profile itself, Memory is /profile/memory, Assistant is on Connect.
  it("shows CV · Memory · Assistant and defaults to CV", () => {
    search = "";
    render(<ProfilePage />);
    expect(replace).not.toHaveBeenCalled(); // CV: /profile itself, no redirect
    expect(goesTo("tab=memory")).toBe("/profile/memory");
    expect(goesTo("tab=assistant")).toBe("/connect?tab=assistant");
  });

  it("?tab=memory opens Memory", () => {
    expect(goesTo("tab=memory")).toBe("/profile/memory");
    expect(goesTo("tab=memory&x=1")).toBe("/profile/memory?x=1");
  });

  it("an old ?tab=setup link opens Memory (Setup lives there now)", () => {
    expect(goesTo("tab=setup")).toBe("/profile/memory");
  });

  it("?tab=assistant opens Assistant configure on Connect your assistant", () => {
    expect(goesTo("tab=assistant")).toBe("/connect?tab=assistant");
    expect(goesTo("tab=assistant&x=1")).toBe("/connect?tab=assistant&x=1");
  });

  it("an unknown tab falls back to CV", () => {
    expect(goesTo("tab=cv")).toBe("/profile");
    expect(goesTo("tab=bogus")).toBe("/profile");
  });

  it("header copy is the approved wording", () => {
    render(<ProfileHeader />);
    expect(screen.getByRole("heading")).toHaveTextContent("What your assistants know about you.");
    expect(screen.getByText(/Nothing here is guessed\. Empty rows stay empty until you or an assistant fills them\./)).toBeInTheDocument();
  });
});
