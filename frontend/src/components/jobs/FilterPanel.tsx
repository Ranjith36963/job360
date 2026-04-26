"use client";

import { useState, useEffect, useRef } from "react";
import { Filter, RotateCcw, Sparkles } from "lucide-react";
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
  visa_sponsorship: undefined,
  action: undefined,
  seniority: undefined,
  employment_type: undefined,
  workplace_type: undefined,
  salary_min: undefined,
  salary_max: undefined,
  industry: undefined,
  staleness_state: undefined,
  sort_by: "score",
  mode: undefined,
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function CheckboxRow({
  label,
  checked,
  onChange,
  description,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  description?: string;
}) {
  return (
    <label className="flex items-start gap-3 cursor-pointer select-none">
      <button
        type="button"
        role="checkbox"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={`mt-0.5 flex h-5 w-5 flex-shrink-0 items-center justify-center rounded border transition-colors ${
          checked
            ? "bg-primary border-primary text-primary-foreground"
            : "border-input bg-transparent hover:border-primary/50"
        }`}
      >
        {checked && (
          <svg viewBox="0 0 14 14" fill="none" className="h-3.5 w-3.5">
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
      <div>
        <span className="text-sm font-medium text-foreground">{label}</span>
        {description && (
          <p className="text-xs text-muted-foreground">{description}</p>
        )}
      </div>
    </label>
  );
}

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
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

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

  // Debounced salary slider — 250ms to avoid per-keystroke refetches
  function handleSalaryChange(field: "salary_min" | "salary_max", value: number) {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    setDraft((d) => ({ ...d, [field]: value || undefined }));
    debounceRef.current = setTimeout(() => {
      setDraft((d) => {
        onFilterChange({ ...d });
        return d;
      });
    }, 250);
  }

  // Cleanup debounce on unmount
  useEffect(() => () => { if (debounceRef.current) clearTimeout(debounceRef.current); }, []);

  return (
    <Sheet open={open} onOpenChange={handleOpenChange}>
      <SheetTrigger
        render={
          <Button
            variant="outline"
            size="sm"
            className="gap-2"
            aria-label={`Open filter panel${activeFilterCount > 0 ? `, ${activeFilterCount} active filters` : ""}`}
          />
        }
      >
        <Filter className="h-3.5 w-3.5" aria-hidden="true" />
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
          {/* ---- Hybrid Mode ---- */}
          <div className="rounded-lg border border-primary/20 bg-primary/5 p-3 space-y-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Sparkles className="h-4 w-4 text-primary" aria-hidden="true" />
                <span className="text-sm font-semibold text-foreground">Hybrid AI Mode</span>
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={draft.mode === "hybrid"}
                aria-label="Hybrid AI Mode"
                onClick={() =>
                  setDraft((d) => ({
                    ...d,
                    mode: d.mode === "hybrid" ? undefined : "hybrid",
                  }))
                }
                className={`relative inline-flex h-5 w-9 flex-shrink-0 rounded-full border-2 border-transparent transition-colors ${
                  draft.mode === "hybrid" ? "bg-primary" : "bg-input"
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                    draft.mode === "hybrid" ? "translate-x-4" : "translate-x-0"
                  }`}
                />
              </button>
            </div>
            <p className="text-xs text-muted-foreground">
              Combines semantic search + keyword matching for smarter results
            </p>
          </div>

          <Separator />

          {/* ---- Sort By ---- */}
          <div className="space-y-2">
            <label className="text-sm font-medium text-foreground">Sort By</label>
            <Select
              value={draft.sort_by ?? "score"}
              onValueChange={(val) =>
                setDraft((d) => ({
                  ...d,
                  sort_by: val as JobFilters["sort_by"],
                }))
              }
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Score" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="score">Best Match Score</SelectItem>
                <SelectItem value="date">Most Recent</SelectItem>
                <SelectItem value="salary">Highest Salary</SelectItem>
                <SelectItem value="staleness">Freshness</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <Separator />

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
              aria-label="Minimum match score"
            />
            <div className="flex justify-between text-xs text-muted-foreground" aria-hidden="true">
              <span>0</span>
              <span>100</span>
            </div>
          </div>

          <Separator />

          {/* ---- Salary Range ---- */}
          <div className="space-y-3">
            <label className="text-sm font-medium text-foreground">
              Salary Range (annual GBP)
            </label>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <Input
                  type="number"
                  placeholder="Min £"
                  value={draft.salary_min ?? ""}
                  onChange={(e) =>
                    handleSalaryChange("salary_min", Number(e.target.value))
                  }
                  aria-label="Minimum salary"
                />
              </div>
              <div>
                <Input
                  type="number"
                  placeholder="Max £"
                  value={draft.salary_max ?? ""}
                  onChange={(e) =>
                    handleSalaryChange("salary_max", Number(e.target.value))
                  }
                  aria-label="Maximum salary"
                />
              </div>
            </div>
          </div>

          <Separator />

          {/* ---- Seniority ---- */}
          <div className="space-y-2">
            <label className="text-sm font-medium text-foreground">Seniority</label>
            <Select
              value={draft.seniority ?? "all"}
              onValueChange={(val) =>
                setDraft((d) => ({
                  ...d,
                  seniority: val === "all" ? undefined : (val || undefined),
                }))
              }
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Any level" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Any level</SelectItem>
                <SelectItem value="intern">Intern</SelectItem>
                <SelectItem value="junior">Junior</SelectItem>
                <SelectItem value="mid">Mid</SelectItem>
                <SelectItem value="senior">Senior</SelectItem>
                <SelectItem value="lead">Lead</SelectItem>
                <SelectItem value="staff">Staff / Principal</SelectItem>
                <SelectItem value="director">Director+</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <Separator />

          {/* ---- Workplace Type ---- */}
          <div className="space-y-2">
            <label className="text-sm font-medium text-foreground">Work Arrangement</label>
            <Select
              value={draft.workplace_type ?? "all"}
              onValueChange={(val) =>
                setDraft((d) => ({
                  ...d,
                  workplace_type: val === "all" ? undefined : (val || undefined),
                }))
              }
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Any" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Any</SelectItem>
                <SelectItem value="remote">Remote</SelectItem>
                <SelectItem value="hybrid">Hybrid</SelectItem>
                <SelectItem value="onsite">On-site</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <Separator />

          {/* ---- Employment Type ---- */}
          <div className="space-y-2">
            <label className="text-sm font-medium text-foreground">Employment Type</label>
            <Select
              value={draft.employment_type ?? "all"}
              onValueChange={(val) =>
                setDraft((d) => ({
                  ...d,
                  employment_type: val === "all" ? undefined : (val || undefined),
                }))
              }
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Any" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Any</SelectItem>
                <SelectItem value="full_time">Full-time</SelectItem>
                <SelectItem value="part_time">Part-time</SelectItem>
                <SelectItem value="contract">Contract</SelectItem>
                <SelectItem value="freelance">Freelance</SelectItem>
                <SelectItem value="internship">Internship</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <Separator />

          {/* ---- Industry ---- */}
          <div className="space-y-2">
            <label className="text-sm font-medium text-foreground">Industry</label>
            <Input
              placeholder="e.g. fintech, healthcare, SaaS..."
              value={draft.industry ?? ""}
              onChange={(e) =>
                setDraft((d) => ({
                  ...d,
                  industry: e.target.value || undefined,
                }))
              }
              aria-label="Filter by industry"
            />
          </div>

          <Separator />

          {/* ---- Source ---- */}
          <div className="space-y-2">
            <label className="text-sm font-medium text-foreground">Source</label>
            <Input
              placeholder="e.g. greenhouse, reed, indeed..."
              value={draft.source ?? ""}
              onChange={(e) =>
                setDraft((d) => ({
                  ...d,
                  source: e.target.value || undefined,
                }))
              }
              aria-label="Filter by job source"
            />
            <p className="text-xs text-muted-foreground">
              Filter to a specific job source
            </p>
          </div>

          <Separator />

          {/* ---- Visa Sponsorship ---- */}
          <CheckboxRow
            label="Visa sponsorship only"
            checked={draft.visa_sponsorship ?? draft.visa_only ?? false}
            onChange={(v) => setDraft((d) => ({ ...d, visa_sponsorship: v, visa_only: v }))}
            description="Only show jobs that offer visa sponsorship"
          />

          <Separator />

          {/* ---- Staleness filter ---- */}
          <div className="space-y-2">
            <label className="text-sm font-medium text-foreground">Listing Freshness</label>
            <Select
              value={draft.staleness_state ?? "all"}
              onValueChange={(val) =>
                setDraft((d) => ({
                  ...d,
                  staleness_state: val === "all" ? undefined : (val || undefined),
                }))
              }
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Any" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Any</SelectItem>
                <SelectItem value="ACTIVE">Active only</SelectItem>
                <SelectItem value="STALE">Include stale</SelectItem>
                <SelectItem value="GHOST">Ghost listings</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <Separator />

          {/* ---- Action filter ---- */}
          <div className="space-y-2">
            <label className="text-sm font-medium text-foreground">My Actions</label>
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
              aria-label="Reset all filters"
            >
              <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
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
