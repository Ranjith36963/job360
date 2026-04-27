"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Briefcase, Clock, Globe, Loader2, Play, RefreshCw, Sparkles, TrendingUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { JobList } from "@/components/jobs/JobList";
import { TimeBuckets } from "@/components/jobs/TimeBuckets";
import { FilterPanel } from "@/components/jobs/FilterPanel";
import {
  getJobs,
  getStatus,
  startSearch,
  getSearchStatus,
  setJobAction,
  removeJobAction,
} from "@/lib/api";
import { queryKeys } from "@/lib/queryKeys";
import { toast } from "@/lib/toast";
import type { JobFilters, JobListResponse, JobResponse, StatusResponse } from "@/lib/types";

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
  const queryClient = useQueryClient();

  // -- Filter / bucket state --
  const [activeBucket, setActiveBucket] = useState("7d");
  const [filters, setFilters] = useState<JobFilters>({
    hours: 168,
    min_score: 30,
  });

  // Keep a ref so the search-complete callback always sees the latest filters
  const filtersRef = useRef(filters);
  filtersRef.current = filters;

  // Search status
  const [searching, setSearching] = useState(false);
  const [searchProgress, setSearchProgress] = useState("");
  const [searchRateLimited, setSearchRateLimited] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Cancel the polling interval on unmount to prevent state updates on a dead component
  useEffect(() => {
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, []);

  // ---------------------------------------------------------------------------
  // TanStack Query — filtered job list
  // ---------------------------------------------------------------------------

  const {
    data: jobsData,
    isFetching: loading,
    refetch: refetchJobs,
  } = useQuery<JobListResponse>({
    queryKey: queryKeys.jobList(filters),
    queryFn: () => getJobs(filters),
    staleTime: 30_000,
    placeholderData: (prev) => prev, // keep previous data while re-fetching
  });

  const jobs = jobsData?.jobs ?? [];
  const total = jobsData?.total ?? 0;

  // ---------------------------------------------------------------------------
  // TanStack Query — unfiltered list for accurate bucket counts
  // ---------------------------------------------------------------------------

  const allJobsKey = useMemo<JobFilters>(
    () => ({ min_score: filters.min_score ?? 30, hours: 168 }),
    [filters.min_score]
  );

  const { data: allJobsData } = useQuery<JobListResponse>({
    queryKey: queryKeys.jobList(allJobsKey),
    queryFn: () => getJobs(allJobsKey),
    staleTime: 30_000,
    placeholderData: (prev) => prev,
  });

  const allJobs = allJobsData?.jobs ?? [];

  // ---------------------------------------------------------------------------
  // TanStack Query — pipeline status (last run time, O1)
  // ---------------------------------------------------------------------------

  const { data: statusData } = useQuery<StatusResponse>({
    queryKey: queryKeys.status(),
    queryFn: getStatus,
    staleTime: 60_000,
    // Best-effort — don't block the page if the backend is slow
    retry: 1,
  });

  // ---------------------------------------------------------------------------
  // Bucket changes
  // ---------------------------------------------------------------------------

  function handleBucketChange(bucket: string) {
    setActiveBucket(bucket);
    const hours = bucketToHours(bucket);
    const next = { ...filters, hours };
    setFilters(next);
    // The useQuery above will automatically re-fetch for the new key
  }

  // ---------------------------------------------------------------------------
  // Filter changes
  // ---------------------------------------------------------------------------

  function handleFilterChange(next: JobFilters) {
    const merged = { ...next, hours: filters.hours };
    setFilters(merged);
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
  // Job actions (like / skip / remove) — optimistic via setQueryData
  // ---------------------------------------------------------------------------

  async function handleAction(jobId: number, action: string) {
    const nextAction = action === "remove" ? null : action;

    // Optimistic update: patch the cached job list in-place
    queryClient.setQueryData<JobListResponse>(queryKeys.jobList(filters), (old) => {
      if (!old) return old;
      return {
        ...old,
        jobs: old.jobs.map((j) =>
          j.id === jobId ? { ...j, action: nextAction } : j
        ),
      };
    });

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
      // Revert by invalidating so TanStack re-fetches the truth
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs() });
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
            // Invalidate all job queries so both lists re-fetch with fresh data
            void queryClient.invalidateQueries({ queryKey: queryKeys.jobs() });
            // Clear message after a few seconds
            setTimeout(() => setSearchProgress(""), 4000);
          }
        } catch {
          // Polling error — keep trying
        }
      }, 3000);
    } catch (err) {
      setSearching(false);
      setSearchProgress("");
      toast.apiError(err, "Failed to start search. Is the backend running?");
      if (err && typeof err === "object" && "status" in err) {
        const { status, retryAfter } = err as { status: number; retryAfter?: number | null };
        if (status === 429) {
          const wait = (retryAfter ?? 60) * 1000;
          setSearchRateLimited(true);
          setTimeout(() => setSearchRateLimited(false), wait);
        }
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Bucket counts (client-side from unfiltered data)
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
            {statusData?.last_run && (
              <p className="text-xs text-muted-foreground/60 mt-1 flex items-center gap-1">
                <Clock className="h-3 w-3" aria-hidden />
                Last run:{" "}
                {new Date(
                  (statusData.last_run as { completed_at?: string }).completed_at ?? ""
                ).toLocaleString()}
              </p>
            )}
          </div>

          <div className="flex items-center gap-2">
            {/* Search progress */}
            {searchProgress && (
              <Badge variant="secondary" className="gap-1.5 animate-fade-in-up">
                {searching && <Loader2 className="h-3 w-3 animate-spin" />}
                {searchProgress}
              </Badge>
            )}

            {/* Search button — disabled while searching or rate-limited (O2) */}
            <Button
              size="sm"
              className="gap-2"
              onClick={handleSearch}
              disabled={searching || searchRateLimited}
              title={searchRateLimited ? "Rate limited — please wait" : undefined}
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
              onClick={() => void refetchJobs()}
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
