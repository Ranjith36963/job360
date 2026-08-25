"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import posthog from "posthog-js";
import { Briefcase, Clock, Download, Globe, Loader2, RefreshCw, Sparkles, TrendingUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { JobList } from "@/components/jobs/JobList";
import { TimeBuckets } from "@/components/jobs/TimeBuckets";
import { SearchingFor } from "@/components/jobs/SearchingFor";
import { FilterPanel, DEFAULT_MIN_SCORE } from "@/components/jobs/FilterPanel";
import {
  getJobs,
  getProfile,
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
import type {
  JobFilters,
  JobListResponse,
  JobResponse,
  ProfileResponse,
  StatusResponse,
} from "@/lib/types";

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
    min_score: DEFAULT_MIN_SCORE,
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

  // ---------------------------------------------------------------------------
  // F2 — empty-window fallback
  // ---------------------------------------------------------------------------
  // The 7-day default window can legitimately come back with zero rows (a
  // crawler gap, a brand-new account, a quiet week). A blank dashboard reads
  // as "broken", not "nothing new this week" — so the first time the 168h
  // window succeeds with 0 rows, we widen the MAIN list query to "all time"
  // (hours: undefined) and say so above the grid.
  //
  // `fallbackTriggeredRef` is a ref, not just the state flag, so the trigger
  // effect below can check-and-set in one synchronous step — it makes a
  // second widen physically impossible, not just unlikely, so this can never
  // loop even if the widened fetch itself comes back empty too.
  const [emptyWeekFallback, setEmptyWeekFallback] = useState(false);
  const fallbackTriggeredRef = useRef(false);

  // Only the MAIN list query widens — the bucket-counts query (allJobsKey,
  // below) is deliberately left on the real 168h window so its counts stay
  // honest (all-zero during a fallback is the true story, not a bug).
  const effectiveFilters = useMemo<JobFilters>(
    () =>
      emptyWeekFallback && filters.hours === 168
        ? { ...filters, hours: undefined }
        : filters,
    [filters, emptyWeekFallback]
  );

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
    // #44: refetch drives the error-state "Retry" button (line ~392).
    refetch: refetchJobs,
  } = useQuery<JobListResponse>({
    queryKey: queryKeys.jobList(effectiveFilters),
    queryFn: () => getJobs(effectiveFilters),
    staleTime: 30_000,
    placeholderData: (prev) => prev, // keep previous data while re-fetching
  });

  // Trigger the ONE widen: a successful (non-error) response, at the real
  // 168h default, with zero rows. Changing `effectiveFilters` above changes
  // the query key, which is what actually drives the re-fetch — this effect
  // only ever flips the flag once.
  useEffect(() => {
    if (
      fallbackTriggeredRef.current ||
      jobsIsError ||
      !jobsData ||
      filters.hours !== 168 ||
      jobsData.jobs.length !== 0
    ) {
      return;
    }
    fallbackTriggeredRef.current = true;
    setEmptyWeekFallback(true);
  }, [jobsData, jobsIsError, filters.hours]);

  // Only show the notice while the user is still looking at the 7d bucket —
  // if they explicitly pick a narrower/other bucket, that's their choice and
  // an honest empty state for THAT window, not this fallback's business.
  const showEmptyWeekNotice = emptyWeekFallback && filters.hours === 168;
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
  // How many rows the min_score filter removed. A hidden job with nothing on
  // screen saying it is hidden reads as "the product found nothing" — which is
  // how 2,830 of one user's 3,236 jobs disappeared silently (measured
  // 2026-07-28). Never hide without saying so.
  const totalUnfiltered = jobsData?.total_unfiltered ?? 0;
  const hiddenByFilters = Math.max(0, totalUnfiltered - total);



  // ---------------------------------------------------------------------------
  // TanStack Query — unfiltered list for accurate bucket counts
  // ---------------------------------------------------------------------------

  const allJobsKey = useMemo<JobFilters>(
    () => ({ min_score: filters.min_score ?? DEFAULT_MIN_SCORE, hours: 168 }),
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

  // ONE signal, used by the subtitle, the Total Matches tile and the list.
  //
  // The list and the bucket counts are two separate requests, so they can
  // disagree — and on production they did: the counts came back in 799ms
  // while the list took 30.7s and arrived empty, leaving the page announcing
  // "No jobs found" and "0 Total Matches" beneath its own badge reading 100,
  // with 226 jobs available. Whenever an empty list contradicts a non-zero
  // count, every part of this screen must say "we could not load them", never
  // "you have none". Declared here rather than inline three times so the three
  // places cannot drift apart.
  // ...but ONLY when the two queries are actually asking the same question.
  // allJobsKey carries just min_score + hours:168. The main list can also carry
  // source, action, visa/seniority/salary/skill narrowing and a shorter window,
  // and under any of those an empty list is a CORRECT answer while the wider
  // count query still returns rows. Comparing them regardless would replace a
  // valid "no matches for this filter" with a false "couldn't load", which is
  // the same class of lie in the opposite direction.
  const NARROWING_KEYS = [
    "source",
    "bucket",
    "action",
    "visa_only",
    "visa_sponsorship",
    "seniority",
    "employment_type",
    "workplace_type",
    "salary_min",
    "salary_max",
    "required_skills",
    "title_canonical",
  ] as const;
  const listIsComparableToCounts =
    effectiveFilters.hours === allJobsKey.hours &&
    (effectiveFilters.min_score ?? DEFAULT_MIN_SCORE) === allJobsKey.min_score &&
    NARROWING_KEYS.every((k) => {
      const v = (effectiveFilters as Record<string, unknown>)[k];
      return v === undefined || v === null || v === "" || v === false;
    });

  const knownAvailable = allJobsData?.total ?? 0;
  const listDisagreesWithCounts =
    !isLoading &&
    !jobsIsError &&
    listIsComparableToCounts &&
    total === 0 &&
    knownAvailable > 0;

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
  // TanStack Query — the search titles we actually send to the job boards
  // ---------------------------------------------------------------------------

  // Purely informational, so it is deliberately the cheapest query on the page:
  // no retry, a long staleTime, and every failure mode is silence. A user with
  // no profile yet gets a 404 here — that is the NORMAL case for a new account,
  // not an error worth showing. `search_titles` is served by the same
  // `generate_search_config` call the pipeline runs, so this line reports what
  // the boards were really asked, not a re-derivation that could drift.
  const { data: profileData } = useQuery<ProfileResponse>({
    queryKey: queryKeys.profile(),
    queryFn: getProfile,
    staleTime: 5 * 60_000,
    retry: false,
  });
  const searchTitles = profileData?.search_titles;

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
    if (filters.min_score && filters.min_score !== DEFAULT_MIN_SCORE) count++;
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
    // effectiveFilters, NOT filters: the empty-window fallback widens the
    // query, and the list on screen is keyed on the WIDENED filters. Patching
    // `filters` would write to a cache nobody is rendering, so a click would
    // appear to do nothing until a refetch (caught in review, PR #273).
    await Promise.all([
      queryClient.cancelQueries({ queryKey: queryKeys.jobList(effectiveFilters) }),
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
    queryClient.setQueryData<JobListResponse>(queryKeys.jobList(effectiveFilters), patchJob);
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

      // Bound the polling. Without a ceiling this retried every 3s forever on a
      // backend crash/deploy/network blip: the page sat on "Search running..."
      // with the Refresh button disabled, and the user had no way to tell
      // "still working" from "silently broken" short of reloading. 10 minutes
      // is well past the slowest observed run (the ATS tier alone is capped at
      // 240s) while still failing in a human timeframe.
      const POLL_INTERVAL_MS = 3000;
      const MAX_POLL_MS = 10 * 60 * 1000;
      const pollStartedAt = Date.now();
      let consecutiveErrors = 0;

      // Poll for status (use filtersRef to avoid stale closure)
      pollRef.current = setInterval(async () => {
        const stopPolling = (message: string) => {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          setSearching(false);
          setSearchProgress("");
          toast.error(message);
        };

        if (Date.now() - pollStartedAt > MAX_POLL_MS) {
          stopPolling(
            "Search is taking longer than expected — we stopped waiting. Your results may still appear; try refreshing."
          );
          return;
        }

        try {
          const status = await getSearchStatus(run_id);
          consecutiveErrors = 0;
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
            if (status.status === "completed") {
              posthog.capture("search_run");
            }
            // Invalidate all job queries so both lists re-fetch with fresh data
            void queryClient.invalidateQueries({ queryKey: queryKeys.jobs() });
            // Clear message after a few seconds
            setTimeout(() => setSearchProgress(""), 4000);
          }
        } catch {
          // Transient errors are expected (a deploy, a blip) so keep trying —
          // but not forever. After ~1 minute of consecutive failures the
          // backend is not coming back within this run, and leaving the user
          // on a spinner is worse than telling them.
          consecutiveErrors += 1;
          if (consecutiveErrors >= 20) {
            stopPolling(
              "Lost contact with the server while searching. Please try again."
            );
          }
        }
      }, POLL_INTERVAL_MS);
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
                <span role="alert" className="text-red-400">
                  Couldn&apos;t load jobs.{" "}
                  <button
                    type="button"
                    onClick={() => refetchJobs()}
                    className="underline hover:no-underline"
                  >
                    Retry
                  </button>
                </span>
              ) : total > 0 ? (
                <>
                  <span className="font-mono text-foreground">{total}</span> jobs
                  matched your profile
                  {hiddenByFilters > 0 && (
                    <>
                      {" · "}
                      <span className="font-mono text-foreground">{hiddenByFilters}</span>{" "}
                      hidden by filters{" "}
                      <button
                        type="button"
                        onClick={() =>
                          setFilters((f) => ({ ...f, min_score: 0 }))
                        }
                        className="underline hover:no-underline"
                      >
                        show all
                      </button>
                    </>
                  )}
                </>
              ) : isLoading ? (
                "Loading jobs..."
              ) : listDisagreesWithCounts ? (
                // Same rule as the list below: never announce zero while the
                // page is holding a count that says otherwise.
                "Couldn't load your matches — try again"
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
              // `total` comes from the main list query. Until that query has
              // answered it is 0, and a hard 0 in a tile labelled "Total
              // Matches" is not a placeholder — it is a factual claim that the
              // product found the user nothing. On production this sat next to
              // "57 New Today / 100 This Week" for the 30s the list took to
              // fail, which is exactly the wrong thing to tell someone with 226
              // matches. An em dash says "not known yet" and cannot be misread.
              {
                label: "Total Matches",
                value: isLoading || listDisagreesWithCounts ? "—" : total,
                icon: Briefcase,
              },
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

        {/* ---- What we actually searched for ----
              Sits directly above the results it explains. Renders nothing at
              all when there are no titles (new account, failed fetch), so it
              can never turn into an empty label or an error. ---- */}
        <SearchingFor titles={searchTitles} />

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
            {/* F2: the 7-day window came back empty, so we silently widened
                to all-time above — say so, plainly, right above the grid it
                explains. */}
            {showEmptyWeekNotice && (
              <p className="mb-2 text-sm text-muted-foreground">
                No new matches this week — showing your most recent matches.
              </p>
            )}
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
            {/* knownAvailable lets the list tell "you have no matches" apart
                from "the list failed to arrive". The bucket-counts query is a
                separate request, so it routinely succeeds when the main list
                does not — on production the counts said 100 while the list
                came back empty after 30.7s, and the page announced "No jobs
                found" over its own badge reading 100. */}
            <JobList
              jobs={jobs}
              loading={isLoading}
              onAction={handleAction}
              knownAvailable={knownAvailable}
              onRetry={() => void refetchJobs()}
            />
          </div>
        )}
      </div>
    </div>
  );
}
