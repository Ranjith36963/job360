/**
 * The page frame (redesign slice 1): signed-in = left sidebar with the
 * EXISTING nav links and labels; signed-out = top bar + footer, no sidebar.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { AppShell } from "@/components/layout/AppShell";

let mockPathname = "/";
const mockAuth: { user: { email: string } | null; loading: boolean } = {
  user: null,
  loading: false,
};

vi.mock("next/navigation", () => ({ usePathname: () => mockPathname }));
vi.mock("@/components/layout/AuthProvider", () => ({
  useAuth: () => ({ ...mockAuth, logout: vi.fn() }),
}));
vi.mock("next-themes", () => ({
  useTheme: () => ({ theme: "system", setTheme: vi.fn() }),
}));

beforeEach(() => {
  mockPathname = "/";
  mockAuth.user = null;
  mockAuth.loading = false;
});

describe("nav-links — stays in step with the middleware", () => {
  it("isProtectedPath agrees with PROTECTED_PATHS in src/middleware.ts", async () => {
    const { readFileSync } = await import("node:fs");
    const { join } = await import("node:path");
    const { isProtectedPath } = await import("@/components/layout/nav-links");
    const src = readFileSync(join(process.cwd(), "src/middleware.ts"), "utf-8");
    const block = src.slice(src.indexOf("PROTECTED_PATHS = ["), src.indexOf("];"));
    const paths = [...block.matchAll(/"(\/[a-z-]*)"/g)].map((m) => m[1]);
    expect(paths).toContain("/needs-you");
    for (const p of paths) expect(isProtectedPath(p)).toBe(true);
    expect(isProtectedPath("/")).toBe(false);
    expect(isProtectedPath("/login")).toBe(false);
  });
});

describe("AppShell", () => {
  it("signed in: a sidebar with the links, account and theme toggle", () => {
    mockAuth.user = { email: "someone@example.com" };
    mockPathname = "/applications";
    render(
      <AppShell>
        <p>page body</p>
      </AppShell>
    );

    const side = screen.getByTestId("app-sidebar");
    const labels = [
      "Needs you",
      "Home",
      "Jobs in",
      "Applications",
      "Outreach",
      "Receipts",
      "Stats",
      "Profile",
      "Connect your assistant",
      "Account",
    ];
    const hrefs = [
      "/needs-you",
      "/",
      "/bring",
      "/applications",
      "/outreach/people",
      "/receipts",
      "/stats",
      "/profile",
      "/connect",
      "/account",
    ];
    labels.forEach((label, i) => {
      expect(within(side).getByRole("link", { name: label })).toHaveAttribute("href", hrefs[i]);
    });
    expect(within(side).getByRole("link", { name: "Applications" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    expect(within(side).queryByRole("link", { name: "Settings" })).toBeNull();
    expect(within(side).getByText("someone@example.com")).toBeInTheDocument();
    expect(within(side).getByRole("button", { name: "Log out" })).toBeInTheDocument();
    expect(within(side).getByRole("group", { name: "Theme" })).toBeInTheDocument();
    expect(screen.getByText("page body")).toBeInTheDocument();
  });

  const EXPECTED_ORDER = [
    "Needs you",
    "Home",
    "Jobs in",
    "Applications",
    "Outreach",
    "Receipts",
    "Stats",
    "Profile",
    "Memory",
    "Connect your assistant",
    "Account",
  ];
  const linkNames = (root: HTMLElement) =>
    within(root)
      .getAllByRole("link")
      .filter((l) => l.getAttribute("aria-label") !== "job360")
      .map((l) => (l.textContent ?? "").replace(/\d+$/, "").trim())
      .filter((t) => t !== "");

  it("sidebar order is exactly the owner's, every item top-level, no People or Settings item", () => {
    mockAuth.user = { email: "someone@example.com" };
    mockPathname = "/applications";
    render(
      <AppShell>
        <p>page body</p>
      </AppShell>
    );
    const side = screen.getByTestId("app-sidebar");
    expect(linkNames(side)).toEqual(EXPECTED_ORDER);
    expect(within(side).queryByRole("link", { name: "Settings" })).toBeNull();
    expect(within(side).queryByRole("link", { name: "People" })).toBeNull();
    expect(within(side).getByRole("link", { name: "Outreach" })).toHaveAttribute("href", "/outreach/people");
    expect(within(side).getByRole("link", { name: "Memory" })).toHaveAttribute("href", "/profile/memory");
    expect(within(side).getByRole("link", { name: "Connect your assistant" })).toHaveAttribute("href", "/connect");
    expect(within(side).getByRole("link", { name: "Account" })).toHaveAttribute("href", "/account");
  });

  it("only the current page's item lights up: Memory, not Profile, on /profile/memory", () => {
    mockAuth.user = { email: "someone@example.com" };
    for (const [path, active] of [
      ["/profile", ["Profile"]],
      ["/profile/memory", ["Memory"]],
      ["/outreach/people", ["Outreach"]],
      ["/connect", ["Connect your assistant"]],
      ["/account", ["Account"]],
    ] as const) {
      mockPathname = path;
      const { unmount } = render(
        <AppShell>
          <p>page body</p>
        </AppShell>
      );
      const side = screen.getByTestId("app-sidebar");
      const current = within(side)
        .getAllByRole("link")
        .filter((l) => l.getAttribute("aria-current") === "page")
        .map((l) => l.textContent);
      expect(current).toEqual(active);
      unmount();
    }
  });

  it("phone menu has the same order", async () => {
    mockAuth.user = { email: "someone@example.com" };
    mockPathname = "/applications";
    render(
      <AppShell>
        <p>page body</p>
      </AppShell>
    );
    fireEvent.click(screen.getByRole("button", { name: "Open navigation menu" }));
    const nav = await screen.findByRole("navigation", { name: "Mobile navigation" });
    expect(linkNames(nav)).toEqual(EXPECTED_ORDER);
  });

  it("Needs you is first, Home second; Home is active only on exactly /", () => {
    mockAuth.user = { email: "someone@example.com" };
    for (const [path, homeActive] of [
      ["/", true],
      ["/applications", false],
      ["/applications/12", false],
      ["/needs-you", false],
    ] as const) {
      mockPathname = path;
      const { unmount } = render(
        <AppShell>
          <p>page body</p>
        </AppShell>
      );
      const side = screen.getByTestId("app-sidebar");
      const links = within(within(side).getByRole("navigation", { name: "Main navigation" })).getAllByRole("link");
      expect(links[0]).toHaveAccessibleName(/^Needs you/);
      expect(links[1]).toHaveAccessibleName("Home");
      if (homeActive) expect(links[1]).toHaveAttribute("aria-current", "page");
      else expect(links[1]).not.toHaveAttribute("aria-current");
      unmount();
    }
  });

  it("signed out: no sidebar; a top bar with a theme toggle, and the footer", () => {
    render(
      <AppShell>
        <p>page body</p>
      </AppShell>
    );

    expect(screen.queryByTestId("app-sidebar")).toBeNull();
    const header = screen.getByRole("banner");
    expect(within(header).getByRole("group", { name: "Theme" })).toBeInTheDocument();
    expect(screen.getByRole("contentinfo")).toBeInTheDocument();
  });

  it("session loading on a guarded route: the sidebar frame is there already, with no links", () => {
    mockAuth.loading = true;
    mockPathname = "/profile";
    render(
      <AppShell>
        <p>page body</p>
      </AppShell>
    );

    const side = screen.getByTestId("app-sidebar");
    expect(within(side).queryByRole("link", { name: "Profile" })).toBeNull();
  });
});
