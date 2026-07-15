"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Briefcase, Clock, Download, Globe, Loader2, RefreshCw, Sparkles, TrendingUp } from "lucide-react";
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
  exportJobsCsv,
} from "@/lib/api";
import { queryKeys } from "@/lib/queryKeys";
import { toast } from "@/lib/toast";
import { apiErrorMessage } from "@/lib/api-error";
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
  // mode:"hybrid" makes the server fuse the keyword score with the semantic
  // re-rank against the user's profile (engine #3). Combined with the stable
  // score-sort below, the result is: highest score first (keyword + enrichment
  // dims), and among equal scores semantic decides the order. All three engines
  // participate. Falls back to keyword order automatically when SEMANTIC_ENABLED
  // is off or the vector index is empty — so this is always safe to request.
  const [filters, setFilters] = useState<JobFilters>({
    hours: 168,
    min_score: 30,
    mode: "hybrid",
  });

  // Keep a ref so the search-complete callback always sees the latest filters
  const filtersRef = useRef(filters);
  useEffect(() => {
    filtersRef.current = filters;
  }, [filters]);

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
    // H10: isFetching stays true during background refetches too — used
    // only to drive a subtle "Refreshing…" indicator, never the skeleton.
    isFetching,
    // Gate the skeleton on isLoading (no data yet at all) — this used to
    // blank the whole grid back to skeletons on every 30s refetch even
    // though the previously-loaded data was perfectly fine to keep showing.
    isLoading,
    // H9: surface fetch failures instead of silently falling through to the
    // "no jobs found" empty state, which reads as "you have zero matches"
    // when the real story is "the request failed".
    isError: jobsIsError,
    error: jobsError,
  } = useQuery<JobListResponse>({
    queryKey: queryKeys.jobList(filters),
    queryFn: () => getJobs(filters),
    staleTime: 30_000,
    placeholderData: (prev) => prev, // keep previous data while re-fetching
  });
  // Background refetch in progress while we already have data on screen.
  const refreshing = isFetching && !isLoading && !jobsIsError;

  // Judge outranks funnel: sort by the LLM fit score when a job has been
  // judged, else the keyword match_score — mirroring the server's
  // COALESCE(llm_fit_score, score) order so the guarantee holds regardless
  // of which path served the data. Array#sort is stable, so equal keys keep
  // the server's secondary (recency) order.
  const jobs = useMemo(
    () =>
      [...(jobsData?.jobs ?? [])].sort(
        (a, b) =>
          (b.llm_fit_score ?? b.match_score ?? 0) -
          (a.llm_fit_score ?? a.match_score ?? 0)
      ),
    [jobsData?.jobs]
  );
  const total = jobsData?.total ?? 0;

  // ---------------------------------------------------------------------------
  // TanStack Query — unfiltered list for accurate bucket counts
  // ---------------------------------------------------------------------------

  const allJobsKey = useMemo<JobFilters>(
    () => ({ min_score: filters.min_score ?? 30, hours: 168 }),
    [filters.min_score]
  );

  const {
    data: allJobsData,
    isError: countsIsError,
    error: countsError,
  } = useQuery<JobListResponse>({
    queryKey: queryKeys.jobList(allJobsKey),
    queryFn: () => getJobs(allJobsKey),
    staleTime: 30_000,
    placeholderData: (prev) => prev,
  });

  const allJobs = useMemo(() => allJobsData?.jobs ?? [], [allJobsData?.jobs]);

  // ---------------------------------------------------------------------------
  // TanStack Query — pipeline status (last run time, O1)
  // ---------------------------------------------------------------------------

  // status is intentionally best-effort (see retry: 1) — a failure here only
  // hides the "Last run" timestamp, it never masquerades as "no jobs found",
  // so it doesn't need its own error banner.
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

    // L8: cancel any in-flight refetch of these exact queries first.
    // Without this, a response that was already in flight can land right
    // after our optimistic write and silently clobber it with stale data.
    await Promise.all([
      queryClient.cancelQueries({ queryKey: queryKeys.jobList(filters) }),
      queryClient.cancelQueries({ queryKey: queryKeys.jobList(allJobsKey) }),
    ]);

    const patchJob = (old: JobListResponse | undefined) => {
      if (!old) return old;
      return {
        ...old,
        jobs: old.jobs.map((j) =>
          j.id === jobId ? { ...j, action: nextAction } : j
        ),
      };
    };

    // Optimistic update: patch the cached job list in-place
    queryClient.setQueryData<JobListResponse>(queryKeys.jobList(filters), patchJob);
    // L8: also patch the unfiltered bucket-counts query so its cached rows
    // don't disagree with the filtered list's action state.
    queryClient.setQueryData<JobListResponse>(queryKeys.jobList(allJobsKey), patchJob);

    try {
      if (action === "remove") {
        await removeJobAction(jobId);
      } else {
        await setJobAction(jobId, {
          action: action as "liked" | "applied" | "not_interested",
          notes: "",
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
  // Auto-search on arrival from the Profile page
  // ---------------------------------------------------------------------------

  // The Profile page's "Search Latest Jobs" button sets a one-shot
  // sessionStorage flag, then navigates here. We read it on mount and kick off
  // a search so the user lands on the dashboard with results already loading.
  // We clear the flag FIRST (synchronously) so Next 16 dev StrictMode's
  // double-effect can't fire two searches; handleSearch's own `if (searching)
  // return` is a second guard.
  useEffect(() => {
    if (sessionStorage.getItem("job360_run_search") === "1") {
      sessionStorage.removeItem("job360_run_search");
      void handleSearch();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
              {jobsIsError ? (
                "Couldn't load jobs — see the error below"
              ) : total > 0 ? (
                <>
                  <span className="font-mono text-foreground">{total}</span> jobs
                  matched your profile
                </>
              ) : isLoading ? (
                "Loading jobs..."
              ) : (
                "No jobs found yet"
              )}
            </p>
            {statusData?.last_run && (() => {
              const ts = (statusData.last_run as { timestamp?: string }).timestamp;
              if (!ts) return null;
              const d = new Date(ts);
              if (Number.isNaN(d.getTime())) return null;
              return (
                <p className="text-xs text-muted-foreground/60 mt-1 flex items-center gap-1">
                  <Clock className="h-3 w-3" aria-hidden />
                  Last run: {d.toLocaleString()}
                </p>
              );
            })()}
          </div>

          <div className="flex items-center gap-2">
            {/* Search progress */}
            {searchProgress && (
              <Badge variant="secondary" className="gap-1.5 animate-fade-in-up">
                {searching && <Loader2 className="h-3 w-3 animate-spin" />}
                {searchProgress}
              </Badge>
            )}

            {/* Refresh — now runs a real search: fetches new jobs from the
                internet (the per-user POST /api/search pipeline), so the list
                picks up anything that appeared since the last run. The full
                "Search Latest Jobs" entry point lives on the Profile page.
                Disabled while a search runs or when rate-limited (O2). */}
            <Button
              variant="outline"
              size="sm"
              className="gap-2"
              onClick={handleSearch}
              disabled={searching || searchRateLimited}
              title={searchRateLimited ? "Rate limited — please wait" : "Fetch the latest jobs"}
            >
              <RefreshCw
                className={`h-3.5 w-3.5 ${searching ? "animate-spin" : ""}`}
              />
              <span className="hidden sm:inline">{searching ? "Refreshing..." : "Refresh"}</span>
            </Button>

            {/* Filters */}
            <FilterPanel
              filters={filters}
              onFilterChange={handleFilterChange}
              activeFilterCount={activeFilterCount}
            />

            {/* Export CSV — downloads the current catalog as a CSV file. */}
            <Button
              variant="outline"
              size="sm"
              className="gap-2"
              onClick={() => {
                void exportJobsCsv().catch((err) =>
                  console.error("CSV export failed", err),
                );
              }}
              title="Export jobs as CSV"
            >
              <Download className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Export</span>
            </Button>
          </div>
        </div>

        {/* Stats summary */}
        <div className="animate-fade-in-up stagger-1 glass-card rounded-xl p-4">
          <div className="relative z-10 grid grid-cols-2 sm:grid-cols-4 gap-4">
            {[
              { label: "Total Matches", value: total, icon: Briefcase },
              { label: "New Today", value: counts["24h"] || 0, icon: Sparkles },
              { label: "This Week", value: counts["7d"] || 0, icon: TrendingUp },
              { label: `Active (${activeBucket.toUpperCase()})`, value: counts[activeBucket] ?? 0, icon: Globe },
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

        {/* ---- Bucket-count error note (H9) ---- */}
        {countsIsError && (
          <p className="text-xs text-destructive">
            {apiErrorMessage(countsError, "Couldn't refresh the time-bucket counts.")}
          </p>
        )}

        {/* ---- Time Buckets ---- */}
        <div className="animate-fade-in-up stagger-2">
          <TimeBuckets
            activeBucket={activeBucket}
            onBucketChange={handleBucketChange}
            counts={counts}
          />
        </div>

        {/* ---- Job list error banner (H9) — a fetch failure must never look
              like "you have zero matches"; render a distinct error state
              instead of falling through to JobList's empty state. Mirrors
              the pipeline page's error banner. ---- */}
        {jobsIsError && (
          <div className="rounded-xl border border-destructive/20 bg-destructive/[0.06] p-4">
            <p className="text-sm text-destructive">
              {apiErrorMessage(jobsError, "Failed to load jobs. Please try again.")}
            </p>
          </div>
        )}

        {/* ---- Job Grid ---- */}
        {!jobsIsError && (
          <div className="animate-fade-in-up stagger-3">
            {/* H10: gate the skeleton on isLoading (no data yet). A background
                refetch (isFetching but data already present) instead shows a
                subtle inline indicator so the grid never blanks out. */}
            {refreshing && (
              <p
                role="status"
                className="mb-2 flex items-center gap-1.5 text-xs text-muted-foreground"
              >
                <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                Refreshing…
              </p>
            )}
            <JobList jobs={jobs} loading={isLoading} onAction={handleAction} />
          </div>
        )}
      </div>
    </div>
  );
}
