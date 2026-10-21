import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ProfileHeader, ProfileTabs } from "./ProfileTabs";

let search = "";
vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams(search) }));

describe("ProfileTabs", () => {
  it("shows CV · Memory · Assistant · Setup and defaults to CV", () => {
    search = "";
    render(<ProfileTabs />);
    expect(screen.getAllByRole("link").map((l) => l.textContent)).toEqual(["CV", "Memory", "Assistant", "Setup"]);
    expect(screen.getByTestId("profile-tab-cv")).toHaveAttribute("aria-current", "page");
    expect(screen.getByTestId("profile-tab-memory")).not.toHaveAttribute("aria-current");
    expect(screen.getByTestId("profile-tab-memory")).toHaveAttribute("href", "/profile?tab=memory");
  });

  it("?tab=memory opens Memory", () => {
    search = "tab=memory";
    render(<ProfileTabs />);
    expect(screen.getByTestId("profile-tab-memory")).toHaveAttribute("aria-current", "page");
    expect(screen.getByTestId("profile-tab-cv")).not.toHaveAttribute("aria-current");
  });

  it("an unknown tab falls back to CV", () => {
    search = "tab=nonsense";
    render(<ProfileTabs />);
    expect(screen.getByTestId("profile-tab-cv")).toHaveAttribute("aria-current", "page");
  });

  it("header copy is the approved wording", () => {
    render(<ProfileHeader />);
    expect(screen.getByRole("heading")).toHaveTextContent("What your assistants know about you.");
    expect(screen.getByText(/Nothing here is guessed\. Empty rows stay empty until you or an assistant fills them\./)).toBeInTheDocument();
  });
});
