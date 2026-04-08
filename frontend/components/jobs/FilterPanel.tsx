"use client";

import { useState } from "react";
import { Filter, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import { Separator } from "@/components/ui/separator";
import {
  Sheet,
  SheetTrigger,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
  SheetFooter,
} from "@/components/ui/sheet";
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from "@/components/ui/select";
import type { JobFilters } from "@/lib/types";

// ---------------------------------------------------------------------------
// Defaults
// ---------------------------------------------------------------------------

const DEFAULT_FILTERS: JobFilters = {
  min_score: 30,
  source: undefined,
  visa_only: false,
  action: undefined,
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface FilterPanelProps {
  filters: JobFilters;
  onFilterChange: (filters: JobFilters) => void;
  activeFilterCount: number;
}

export function FilterPanel({
  filters,
  onFilterChange,
  activeFilterCount,
}: FilterPanelProps) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<JobFilters>(filters);

  // Sync draft when panel opens
  function handleOpenChange(next: boolean) {
    if (next) setDraft(filters);
    setOpen(next);
  }

  function applyFilters() {
    onFilterChange(draft);
    setOpen(false);
  }

  function resetFilters() {
    const reset = { ...DEFAULT_FILTERS, hours: filters.hours };
    setDraft(reset);
    onFilterChange(reset);
    setOpen(false);
  }

  return (
    <Sheet open={open} onOpenChange={handleOpenChange}>
      <SheetTrigger
        render={
          <Button
            variant="outline"
            size="sm"
            className="gap-2"
          />
        }
      >
        <Filter className="h-3.5 w-3.5" />
        Filters
        {activeFilterCount > 0 && (
          <span className="flex items-center justify-center rounded-full bg-primary/20 text-primary text-xs font-mono w-5 h-5">
            {activeFilterCount}
          </span>
        )}
      </SheetTrigger>

      <SheetContent side="right" className="w-80 sm:w-96 p-0">
        <SheetHeader className="p-4 pb-0">
          <SheetTitle>Filter Jobs</SheetTitle>
          <SheetDescription>
            Narrow down results to find the best matches.
          </SheetDescription>
        </SheetHeader>

        <div className="flex-1 overflow-y-auto px-4 py-4 space-y-6">
          {/* ---- Min Score ---- */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <label className="text-sm font-medium text-foreground">
                Minimum Score
              </label>
              <span className="font-mono text-sm text-primary tabular-nums">
                {draft.min_score ?? 0}
              </span>
            </div>
            <Slider
              min={0}
              max={100}
              value={[draft.min_score ?? 0]}
              onValueChange={(val) => {
                const v = Array.isArray(val) ? val[0] : val;
                setDraft((d) => ({ ...d, min_score: v }));
              }}
            />
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>0</span>
              <span>100</span>
            </div>
          </div>

          <Separator />

          {/* ---- Source ---- */}
          <div className="space-y-2">
            <label className="text-sm font-medium text-foreground">
              Source
            </label>
            <Input
              placeholder="e.g. greenhouse, reed, indeed..."
              value={draft.source ?? ""}
              onChange={(e) =>
                setDraft((d) => ({
                  ...d,
                  source: e.target.value || undefined,
                }))
              }
            />
            <p className="text-xs text-muted-foreground">
              Filter to a specific job source
            </p>
          </div>

          <Separator />

          {/* ---- Visa Only ---- */}
          <div className="space-y-2">
            <label className="flex items-center gap-3 cursor-pointer select-none">
              <button
                type="button"
                role="checkbox"
                aria-checked={draft.visa_only ?? false}
                onClick={() =>
                  setDraft((d) => ({ ...d, visa_only: !d.visa_only }))
                }
                className={`flex h-5 w-5 items-center justify-center rounded border transition-colors ${
                  draft.visa_only
                    ? "bg-primary border-primary text-primary-foreground"
                    : "border-input bg-transparent hover:border-primary/50"
                }`}
              >
                {draft.visa_only && (
                  <svg
                    viewBox="0 0 14 14"
                    fill="none"
                    className="h-3.5 w-3.5"
                  >
                    <path
                      d="M11.5 4L5.5 10L2.5 7"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                )}
              </button>
              <span className="text-sm font-medium text-foreground">
                Visa sponsorship only
              </span>
            </label>
            <p className="text-xs text-muted-foreground pl-8">
              Only show jobs that mention visa sponsorship
            </p>
          </div>

          <Separator />

          {/* ---- Action filter ---- */}
          <div className="space-y-2">
            <label className="text-sm font-medium text-foreground">
              My Actions
            </label>
            <Select
              value={draft.action ?? "all"}
              onValueChange={(val) =>
                setDraft((d) => ({
                  ...d,
                  action: !val || val === "all" ? undefined : val,
                }))
              }
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder="All jobs" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All jobs</SelectItem>
                <SelectItem value="liked">Liked</SelectItem>
                <SelectItem value="applied">Applied</SelectItem>
                <SelectItem value="not_interested">Not Interested</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        <SheetFooter className="border-t border-border/50 p-4">
          <div className="flex items-center gap-2 w-full">
            <Button
              variant="ghost"
              size="sm"
              className="gap-1.5 text-muted-foreground"
              onClick={resetFilters}
            >
              <RotateCcw className="h-3.5 w-3.5" />
              Reset
            </Button>
            <Button
              size="sm"
              className="ml-auto gap-1.5"
              onClick={applyFilters}
            >
              Apply Filters
            </Button>
          </div>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
