/**
 * The page frame (redesign slice 1): signed-in = left sidebar with the
 * EXISTING nav links and labels; signed-out = top bar + footer, no sidebar.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
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
  it("signed in: a sidebar with the existing links, Settings, account and theme toggle", () => {
    mockAuth.user = { email: "someone@example.com" };
    mockPathname = "/applications";
    render(
      <AppShell>
        <p>page body</p>
      </AppShell>
    );

    const side = screen.getByTestId("app-sidebar");
    const labels = [
      "Profile",
      "Bring a job",
      "Applications",
      "Needs you",
      "Receipts",
      "Connect your assistant",
    ];
    const hrefs = [
      "/profile",
      "/bring",
      "/applications",
      "/needs-you",
      "/receipts",
      "/settings/connect",
    ];
    labels.forEach((label, i) => {
      expect(within(side).getByRole("link", { name: label })).toHaveAttribute("href", hrefs[i]);
    });
    expect(within(side).getByRole("link", { name: "Applications" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    expect(within(side).getByRole("link", { name: "Settings" })).toBeInTheDocument();
    expect(within(side).getByText("someone@example.com")).toBeInTheDocument();
    expect(within(side).getByRole("button", { name: "Log out" })).toBeInTheDocument();
    expect(within(side).getByRole("group", { name: "Theme" })).toBeInTheDocument();
    expect(screen.getByText("page body")).toBeInTheDocument();
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
