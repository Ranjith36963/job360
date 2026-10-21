/**
 * Redesign slice 1: signed-in content is no longer boxed at `max-w-7xl`. It
 * fills the area right of the sidebar with comfortable side padding
 * (`px-6 lg:px-10`). This pins that behaviour: no max-width cap on the
 * container, the new padding, and caller classes preserved.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { PageContainer } from "@/components/layout/PageContainer";

const MAX_W_RE = /\bmax-w-/;

describe("PageContainer — full width beside the sidebar", () => {
  it("fills the available width with px-6 lg:px-10 padding and no max-width box", () => {
    render(
      <PageContainer>
        <p>content</p>
      </PageContainer>
    );

    const container = screen.getByText("content").parentElement!;
    for (const cls of ["w-full", "px-6", "lg:px-10"]) {
      expect(container).toHaveClass(cls);
    }
    expect(container.className).not.toMatch(MAX_W_RE);
    expect(container).not.toHaveClass("mx-auto");
  });

  it("keeps caller classes alongside the shared padding", () => {
    render(
      <PageContainer className="flex flex-col gap-6 py-8">
        <p>content</p>
      </PageContainer>
    );

    const container = screen.getByText("content").parentElement!;
    for (const cls of ["px-6", "lg:px-10", "flex", "flex-col", "gap-6", "py-8"]) {
      expect(container).toHaveClass(cls);
    }
  });
});
