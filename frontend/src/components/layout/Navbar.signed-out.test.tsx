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
import { render, screen } from "@testing-library/react";
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

const APP_LINKS = ["Profile", "Dashboard", "Pipeline", "Channels"];

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
  it("shows the app links and hides the marketing CTAs", () => {
    mockAuth.user = { email: "someone@example.com" };
    render(<Navbar />);

    for (const label of APP_LINKS) {
      expect(screen.getAllByRole("link", { name: label }).length).toBeGreaterThan(0);
    }
    expect(screen.queryByRole("link", { name: /get started/i })).toBeNull();
    expect(screen.getAllByText("someone@example.com").length).toBeGreaterThan(0);
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
});
