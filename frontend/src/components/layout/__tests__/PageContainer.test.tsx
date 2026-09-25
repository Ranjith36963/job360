/**
 * Owner decision (2026-09-25): every signed-in page shares ONE width —
 * exactly the navbar's max width and horizontal padding — so page edges
 * line up with the menu above them. This pins PageContainer to those exact
 * classes and guards against the navbar's width drifting away from it
 * unnoticed.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { PageContainer } from "@/components/layout/PageContainer";
import { Navbar } from "@/components/layout/Navbar";

vi.mock("next/navigation", () => ({
  usePathname: () => "/applications",
}));

vi.mock("@/components/layout/AuthProvider", () => ({
  useAuth: () => ({ user: null, loading: false, logout: vi.fn() }),
}));

const SHARED_WIDTH_CLASSES = ["mx-auto", "max-w-7xl", "px-4", "sm:px-6"];

describe("PageContainer — shared page width", () => {
  it("uses exactly the navbar's max width and horizontal padding", () => {
    render(
      <PageContainer>
        <p>content</p>
      </PageContainer>
    );

    const content = screen.getByText("content");
    const container = content.parentElement;
    expect(container).not.toBeNull();
    for (const cls of SHARED_WIDTH_CLASSES) {
      expect(container!.className).toContain(cls);
    }
  });

  it("keeps caller classes alongside the shared width", () => {
    render(
      <PageContainer className="flex flex-col gap-6 py-8">
        <p>content</p>
      </PageContainer>
    );

    const container = screen.getByText("content").parentElement!;
    for (const cls of [...SHARED_WIDTH_CLASSES, "flex", "flex-col", "gap-6", "py-8"]) {
      expect(container.className).toContain(cls);
    }
  });

  it("matches the navbar's own max-width classes (edges line up)", () => {
    render(<Navbar />);
    const nav = screen.getByRole("banner").firstElementChild;
    expect(nav).not.toBeNull();
    for (const cls of SHARED_WIDTH_CLASSES) {
      expect(nav!.className).toContain(cls);
    }
  });
});
