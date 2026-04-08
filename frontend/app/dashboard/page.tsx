"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Briefcase, Globe, Loader2, Play, RefreshCw, Sparkles, TrendingUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { JobList } from "@/components/jobs/JobList";
import { TimeBuckets } from "@/components/jobs/TimeBuckets";
import { FilterPanel } from "@/components/jobs/FilterPanel";
import {
  getJobs,
  startSearch,
  getSearchStatus,
  setJobAction,
  removeJobAction,
} from "@/lib/api";
import type { JobFilters, JobResponse } from "@/lib/types";

// ---------------------------------------------------------------------------
// Bucket helpers — count jobs client-side by age
// ---------------------------------------------------------------------------

function hoursSince(dateStr: string): number {
  return (Date.now() - new Date(dateStr).getTime()) / 3_600_000;
}

function bucketCounts(jobs: JobResponse[]): Record<string, number> {
  const counts: Record<string, number> = { all: jobs.length };
  const thresholds = [
    { key: "24h", hours: 24 },
    { key: "48h", hours: 48 },
    { key: "3d", hours: 72 },
    { key: "5d", hours: 120 },
    { key: "7d", hours: 168 },
  ];
  for (const { key, hours } of thresholds) {
    counts[key] = jobs.filter((j) => hoursSince(j.date_found) <= hours).length;
  }
  return counts;
}

function bucketToHours(bucket: string): number | undefined {
  const map: Record<string, number> = {
    "24h": 24,
    "48h": 48,
    "3d": 72,
    "5d": 120,
    "7d": 168,
  };
  return map[bucket]; // "all" returns undefined
}

// ---------------------------------------------------------------------------
// Page Component
// ---------------------------------------------------------------------------

export default function DashboardPage() {
  // -- State --
  const [jobs, setJobs] = useState<JobResponse[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [activeBucket, setActiveBucket] = useState("7d");
  const [filters, setFilters] = useState<JobFilters>({
    hours: 168,
    min_score: 30,
  });

  // All jobs (unfiltered by bucket) for accurate bucket counts
  const [allJobs, setAllJobs] = useState<JobResponse[]>([]);

  // Search status
  const [searching, setSearching] = useState(false);
  const [searchProgress, setSearchProgress] = useState("");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const filtersRef = useRef(filters);
  filtersRef.current = filters;

  // ---------------------------------------------------------------------------
  // Fetch jobs
  // ---------------------------------------------------------------------------

  const fetchJobs = useCallback(async (f: JobFilters) => {
    setLoading(true);
    try {
      const data = await getJobs(f);
      setJobs(data.jobs);
      setTotal(data.total);
      // Also fetch unfiltered for accurate bucket counts
      const allData = await getJobs({ min_score: f.min_score ?? 30, hours: 168 });
      setAllJobs(allData.jobs);
    } catch (err) {
      console.error("Failed to fetch jobs:", err);
      setJobs([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, []);

  // Initial load
  useEffect(() => {
    fetchJobs(filters);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  // ---------------------------------------------------------------------------
  // Bucket changes
  // ---------------------------------------------------------------------------

  function handleBucketChange(bucket: string) {
    setActiveBucket(bucket);
    const hours = bucketToHours(bucket);
    const next = { ...filters, hours };
    setFilters(next);
    fetchJobs(next);
  }

  // ---------------------------------------------------------------------------
  // Filter changes
  // ---------------------------------------------------------------------------

  function handleFilterChange(next: JobFilters) {
    const merged = { ...next, hours: filters.hours };
    setFilters(merged);
    fetchJobs(merged);
  }

  // Count active non-default filters (excluding hours/bucket)
  const activeFilterCount = useMemo(() => {
    let count = 0;
    if (filters.min_score && filters.min_score !== 30) count++;
    if (filters.source) count++;
    if (filters.visa_only) count++;
    if (filters.action) count++;
    return count;
  }, [filters]);

  // ---------------------------------------------------------------------------
  // Job actions (like / skip / remove)
  // ---------------------------------------------------------------------------

  async function handleAction(jobId: number, action: string) {
    // Optimistic update
    setJobs((prev) =>
      prev.map((j) =>
        j.id === jobId
          ? { ...j, action: action === "remove" ? null : action }
          : j
      )
    );

    try {
      if (action === "remove") {
        await removeJobAction(jobId);
      } else {
        await setJobAction(jobId, {
          action: action as "liked" | "applied" | "not_interested",
        });
      }
    } catch (err) {
      console.error("Action failed:", err);
      // Revert on failure
      fetchJobs(filters);
    }
  }

  // ---------------------------------------------------------------------------
  // Search trigger
  // ---------------------------------------------------------------------------

  async function handleSearch() {
    if (searching) return;
    setSearching(true);
    setSearchProgress("Starting search...");

    try {
      const { run_id } = await startSearch({ safe: false });
      setSearchProgress("Search running...");

      // Clear any existing poll before starting new one
      if (pollRef.current) clearInterval(pollRef.current);

      // Poll for status (use filtersRef to avoid stale closure)
      pollRef.current = setInterval(async () => {
        try {
          const status = await getSearchStatus(run_id);
          setSearchProgress(status.progress || status.status);

          if (status.status === "completed" || status.status === "failed") {
            if (pollRef.current) clearInterval(pollRef.current);
            pollRef.current = null;
            setSearching(false);
            setSearchProgress(
              status.status === "completed"
                ? "Search complete!"
                : "Search failed"
            );
            // Auto-refresh job list (use ref to get latest filters)
            fetchJobs(filtersRef.current);
            // Clear message after a few seconds
            setTimeout(() => setSearchProgress(""), 4000);
          }
        } catch {
          // Polling error — keep trying
        }
      }, 3000);
    } catch (err) {
      console.error("Search failed:", err);
      setSearching(false);
      setSearchProgress("Failed to start search");
      setTimeout(() => setSearchProgress(""), 4000);
    }
  }

  // ---------------------------------------------------------------------------
  // Bucket counts (client-side from current data)
  // ---------------------------------------------------------------------------

  const counts = useMemo(() => bucketCounts(allJobs), [allJobs]);

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="relative">
      {/* Search progress bar */}
      {searching && (
        <div className="absolute top-0 left-0 right-0 h-0.5 z-20 overflow-hidden rounded-full">
          <div className="h-full w-full bg-primary animate-shimmer" />
        </div>
      )}

      {/* Aurora atmosphere */}
      <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="aurora-glow-top aurora-glow-animated" />
        <div className="aurora-glow-left" />
        <div className="aurora-glow-right" />
      </div>

      <div className="mx-auto max-w-7xl px-4 sm:px-6 py-6 space-y-6">
        {/* ---- Header ---- */}
        <div className="animate-fade-in-up flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div>
            <h1 className="font-heading text-3xl font-bold tracking-tight">
              <span className="text-gradient-lime">Dashboard</span>
            </h1>
            <p className="text-sm text-muted-foreground mt-0.5">
              {total > 0 ? (
                <>
                  <span className="font-mono text-foreground">{total}</span> jobs
                  matched your profile
                </>
              ) : loading ? (
                "Loading jobs..."
              ) : (
                "No jobs found yet"
              )}
            </p>
          </div>

          <div className="flex items-center gap-2">
            {/* Search progress */}
            {searchProgress && (
              <Badge variant="secondary" className="gap-1.5 animate-fade-in-up">
                {searching && <Loader2 className="h-3 w-3 animate-spin" />}
                {searchProgress}
              </Badge>
            )}

            {/* Search button */}
            <Button
              size="sm"
              className="gap-2"
              onClick={handleSearch}
              disabled={searching}
            >
              {searching ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Play className="h-3.5 w-3.5" />
              )}
              {searching ? "Searching..." : "New Search"}
            </Button>

            {/* Refresh */}
            <Button
              variant="outline"
              size="sm"
              className="gap-2"
              onClick={() => fetchJobs(filters)}
              disabled={loading}
            >
              <RefreshCw
                className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`}
              />
              <span className="hidden sm:inline">Refresh</span>
            </Button>

            {/* Filters */}
            <FilterPanel
              filters={filters}
              onFilterChange={handleFilterChange}
              activeFilterCount={activeFilterCount}
            />
          </div>
        </div>

        {/* Stats summary */}
        <div className="animate-fade-in-up stagger-1 glass-card rounded-xl p-4">
          <div className="relative z-10 grid grid-cols-2 sm:grid-cols-4 gap-4">
            {[
              { label: "Total Matches", value: total, icon: Briefcase },
              { label: "New Today", value: counts["24h"] || 0, icon: Sparkles },
              { label: "This Week", value: counts["7d"] || 0, icon: TrendingUp },
              { label: "Active Bucket", value: activeBucket.toUpperCase(), icon: Globe },
            ].map(({ label, value: v, icon: Icon }) => (
              <div key={label} className="flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 ring-1 ring-primary/20">
                  <Icon className="h-5 w-5 text-primary" />
                </div>
                <div>
                  <p className="font-mono text-xl font-bold text-foreground">{v}</p>
                  <p className="text-xs text-muted-foreground">{label}</p>
                </div>
              </div>
            ))}
          </div>
        </div>

        <Separator />

        {/* ---- Time Buckets ---- */}
        <div className="animate-fade-in-up stagger-2">
          <TimeBuckets
            activeBucket={activeBucket}
            onBucketChange={handleBucketChange}
            counts={counts}
          />
        </div>

        {/* ---- Job Grid ---- */}
        <div className="animate-fade-in-up stagger-3">
          <JobList jobs={jobs} loading={loading} onAction={handleAction} />
        </div>
      </div>
    </div>
  );
}
