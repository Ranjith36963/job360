/**
 * The navbar used to render its four app links (Profile, Dashboard, Pipeline,
 * Channels) and the Settings gear unconditionally, while the only signed-in-gated
 * element was the email + logout pair. Every one of those links points at a route
 * src/middleware.ts guards, so a signed-out visitor to the landing page — the
 * first thing anyone sees — got five controls that could only bounce them to
 * /login, and no way at all to sign in or sign up.
 *
 * Found by walking live job360.uk with tests/design/design-pass.mjs and looking
 * at the screenshots: the shots of BOTH the landing page and the login page show
 * the signed-in navigation.
 *
 * These tests pin the three states apart: signed in, signed out, and the
 * still-loading state in between (which must not flash marketing CTAs at a
 * returning user before their session resolves).
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Navbar } from "./Navbar";

let mockPathname = "/";
const mockAuth: { user: { email: string } | null; loading: boolean } = {
  user: null,
  loading: false,
};

vi.mock("next/navigation", () => ({
  usePathname: () => mockPathname,
}));

vi.mock("@/components/layout/AuthProvider", () => ({
  useAuth: () => ({ ...mockAuth, logout: vi.fn() }),
}));

// R14 (docs/plans/2026-09-04-application-spine) — Applications is the
// spine-home nav item. Pipeline LEFT the nav under R14 — the URL still works,
// just not linked from here (C3, application-spine review). Receipts left
// too, but a new-user walk (2026-09-27) found no other way to discover it,
// so it is back. Dashboard was deleted outright in slice 5
// (delete-sourcing-era); Channels was deleted outright in the mission sweep
// (notifications are pull-not-push, VISION:133).
const APP_LINKS = ["Profile", "Applications", "Receipts"];

beforeEach(() => {
  mockPathname = "/";
  mockAuth.user = null;
  mockAuth.loading = false;
});

describe("Navbar — signed out", () => {
  it("offers a way in instead of links that only redirect to /login", () => {
    render(<Navbar />);

    for (const label of APP_LINKS) {
      expect(screen.queryByRole("link", { name: label })).toBeNull();
    }
    expect(screen.queryByLabelText("Settings")).toBeNull();

    // The actual regression: there was no sign-in affordance anywhere.
    expect(screen.getAllByRole("link", { name: /log in/i }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: /get started/i }).length).toBeGreaterThan(0);
  });

  it("does not offer 'Log in' while already on /login", () => {
    mockPathname = "/login";
    render(<Navbar />);

    expect(screen.queryByRole("link", { name: /^log in$/i })).toBeNull();
    expect(screen.getAllByRole("link", { name: /get started/i }).length).toBeGreaterThan(0);
  });

  it("does not offer 'Get started' while already on /register", () => {
    mockPathname = "/register";
    render(<Navbar />);

    expect(screen.queryByRole("link", { name: /get started/i })).toBeNull();
  });
});

describe("Navbar — signed in", () => {
  // Redesign slice 1: from md up the sidebar owns the app links and the top
  // bar hides itself; below md the top bar stays and its drawer holds them.
  it("is a phone-only bar: hidden from md up, with a drawer trigger and no marketing CTAs", () => {
    mockAuth.user = { email: "someone@example.com" };
    render(<Navbar />);

    expect(screen.getByRole("banner")).toHaveClass("md:hidden");
    expect(screen.getByRole("button", { name: /open navigation menu/i })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /get started/i })).toBeNull();
    expect(screen.queryByRole("link", { name: /log in/i })).toBeNull();
  });

  it("the drawer holds the app links, Settings, the account and the theme toggle", () => {
    mockAuth.user = { email: "someone@example.com" };
    render(<Navbar />);

    fireEvent.click(screen.getByRole("button", { name: /open navigation menu/i }));

    for (const label of APP_LINKS) {
      expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
    }
    expect(screen.getByRole("link", { name: "Settings" })).toBeInTheDocument();
    expect(screen.getByText("someone@example.com")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Log out" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Theme" })).toBeInTheDocument();
  });
});

describe("Navbar — session still loading", () => {
  it("shows neither set, so a returning user never sees a sign-up flash", () => {
    mockAuth.loading = true;
    render(<Navbar />);

    expect(screen.queryByRole("link", { name: "Dashboard" })).toBeNull();
    expect(screen.queryByRole("link", { name: /get started/i })).toBeNull();
    expect(screen.queryByRole("link", { name: /log in/i })).toBeNull();
  });

  it("on a guarded route it already yields to the sidebar on desktop (no jump when the user arrives)", () => {
    mockAuth.loading = true;
    mockPathname = "/applications";
    render(<Navbar />);

    expect(screen.getByRole("banner")).toHaveClass("md:hidden");
  });
});
