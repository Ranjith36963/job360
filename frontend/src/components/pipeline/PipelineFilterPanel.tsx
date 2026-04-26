"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { PipelineApplication } from "@/lib/types";

// ---------------------------------------------------------------------------
// PipelineFilterPanel — controlled filter bar above the Kanban board
// ---------------------------------------------------------------------------

interface PipelineFilterPanelProps {
  applications: PipelineApplication[];
  onFilter: (filtered: PipelineApplication[]) => void;
}

export function PipelineFilterPanel({
  applications,
  onFilter,
}: PipelineFilterPanelProps) {
  const [company, setCompany] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  // Apply filters whenever inputs or source data change
  useEffect(() => {
    let result = applications;

    if (company.trim()) {
      const lower = company.trim().toLowerCase();
      result = result.filter((app) =>
        (app.company ?? "").toLowerCase().includes(lower) ||
        (app.title ?? "").toLowerCase().includes(lower)
      );
    }

    if (dateFrom) {
      const from = new Date(dateFrom).getTime();
      result = result.filter(
        (app) => new Date(app.created_at).getTime() >= from
      );
    }

    if (dateTo) {
      // Include the entire "to" day by advancing to end-of-day
      const to = new Date(dateTo).getTime() + 86_400_000 - 1;
      result = result.filter(
        (app) => new Date(app.created_at).getTime() <= to
      );
    }

    onFilter(result);
  }, [applications, company, dateFrom, dateTo, onFilter]);

  function clearFilters() {
    setCompany("");
    setDateFrom("");
    setDateTo("");
  }

  const hasFilters = company !== "" || dateFrom !== "" || dateTo !== "";

  return (
    <div className="mb-4 flex flex-wrap items-end gap-3 rounded-xl border border-border/40 bg-card/30 px-4 py-3">
      {/* Company / title search */}
      <div className="flex flex-col gap-1 min-w-[180px] flex-1">
        <label className="text-xs text-muted-foreground font-medium">
          Company / Title
        </label>
        <Input
          type="text"
          placeholder="Filter by company or title…"
          value={company}
          onChange={(e) => setCompany(e.target.value)}
          className="h-8"
        />
      </div>

      {/* Applied from */}
      <div className="flex flex-col gap-1">
        <label className="text-xs text-muted-foreground font-medium">
          Applied from
        </label>
        <Input
          type="date"
          value={dateFrom}
          onChange={(e) => setDateFrom(e.target.value)}
          className="h-8 w-36"
        />
      </div>

      {/* Applied to */}
      <div className="flex flex-col gap-1">
        <label className="text-xs text-muted-foreground font-medium">
          Applied to
        </label>
        <Input
          type="date"
          value={dateTo}
          onChange={(e) => setDateTo(e.target.value)}
          className="h-8 w-36"
        />
      </div>

      {/* Clear filters */}
      {hasFilters && (
        <Button
          size="sm"
          variant="ghost"
          onClick={clearFilters}
          className="h-8 text-xs text-muted-foreground hover:text-foreground self-end"
        >
          Clear filters
        </Button>
      )}
    </div>
  );
}
