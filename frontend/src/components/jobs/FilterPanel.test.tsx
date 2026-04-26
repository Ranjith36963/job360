import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FilterPanel } from "./FilterPanel";
import type { JobFilters } from "@/lib/types";

const defaultFilters: JobFilters = {
  min_score: 30,
  sort_by: "score",
};

describe("FilterPanel", () => {
  it("renders filter button with active count", () => {
    render(
      <FilterPanel
        filters={defaultFilters}
        onFilterChange={vi.fn()}
        activeFilterCount={3}
      />
    );
    expect(screen.getByRole("button", { name: /open filter panel.*3 active/i })).toBeInTheDocument();
  });

  it("renders filter button without count when no active filters", () => {
    render(
      <FilterPanel
        filters={defaultFilters}
        onFilterChange={vi.fn()}
        activeFilterCount={0}
      />
    );
    const btn = screen.getByRole("button", { name: /open filter panel$/i });
    expect(btn).toBeInTheDocument();
  });

  it("opens filter sheet when button clicked", async () => {
    render(
      <FilterPanel
        filters={defaultFilters}
        onFilterChange={vi.fn()}
        activeFilterCount={0}
      />
    );
    await userEvent.click(screen.getByRole("button", { name: /open filter panel/i }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("shows Hybrid AI Mode toggle", async () => {
    render(
      <FilterPanel
        filters={defaultFilters}
        onFilterChange={vi.fn()}
        activeFilterCount={0}
      />
    );
    await userEvent.click(screen.getByRole("button", { name: /open filter panel/i }));
    expect(screen.getByText("Hybrid AI Mode")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: /hybrid ai mode/i })).toBeInTheDocument();
  });
});
