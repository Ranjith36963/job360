"use client";

import { RefreshCw, SearchX } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/ui/empty-state";
import { JobCard } from "@/components/jobs/JobCard";
import type { JobResponse } from "@/lib/types";

// ---------------------------------------------------------------------------
// Skeleton card for loading state
// ---------------------------------------------------------------------------

function JobCardSkeleton() {
  return (
    <div className="glass-card rounded-xl p-4 flex flex-col gap-3">
      <div className="flex items-start gap-3">
        <Skeleton className="w-11 h-11 rounded-lg flex-shrink-0" />
        <div className="flex-1 space-y-2">
          <Skeleton className="h-5 w-3/4" />
          <Skeleton className="h-4 w-1/2" />
        </div>
      </div>
      <div className="flex gap-2">
        <Skeleton className="h-5 w-20 rounded-full" />
        <Skeleton className="h-5 w-16 rounded-full" />
      </div>
      <div className="flex gap-1.5">
        <Skeleton className="h-6 w-16 rounded-md" />
        <Skeleton className="h-6 w-20 rounded-md" />
        <Skeleton className="h-6 w-14 rounded-md" />
      </div>
      <div className="flex gap-2 pt-1 border-t border-border/50">
        <Skeleton className="h-7 w-16 rounded-md" />
        <Skeleton className="h-7 w-14 rounded-md" />
        <Skeleton className="h-7 w-14 rounded-md" />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface JobListProps {
  jobs: JobResponse[];
  loading: boolean;
  onAction: (jobId: number, action: string) => void;
  /**
   * How many jobs the page's OWN bucket-count query says exist for this window.
   *
   * The two queries can disagree, and when they do the honest answer is not
   * "no jobs". Observed on production: the list request took 30.7s and came
   * back with an empty payload while the counts request beside it succeeded,
   * so the dashboard rendered "No jobs found" and "0 Total Matches" directly
   * underneath its own tab badge reading 100 — with 226 matching jobs sitting
   * in the API the whole time. A user reads that as "this product found me
   * nothing", which is the opposite of true.
   */
  knownAvailable?: number;
  /** Lets that state offer a way out instead of stranding the user. */
  onRetry?: () => void;
}

export function JobList({
  jobs,
  loading,
  onAction,
  knownAvailable = 0,
  onRetry,
}: JobListProps) {
  if (loading) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className={`animate-fade-in-up stagger-${i + 1}`}>
            <JobCardSkeleton />
          </div>
        ))}
      </div>
    );
  }

  // The list is empty but the page's own counts say it should not be. Never
  // claim zero matches when we are holding a number that says otherwise —
  // say the results failed to load, and offer the retry.
  if (jobs.length === 0 && knownAvailable > 0) {
    return (
      <EmptyState
        icon={<SearchX className="h-8 w-8" />}
        title="Couldn't load your matches"
        description={`${knownAvailable} ${knownAvailable === 1 ? "job matches" : "jobs match"} this time range, but the list didn't come back. This is usually a slow response — try again.`}
        action={
          onRetry ? (
            <Button variant="outline" size="sm" onClick={onRetry} className="gap-2">
              <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
              Try again
            </Button>
          ) : undefined
        }
      />
    );
  }

  if (jobs.length === 0) {
    return (
      <EmptyState
        icon={<SearchX className="h-8 w-8" />}
        title="No jobs found"
        description="Try adjusting your filters, expanding the time range, or lowering the minimum score. You can also run a new search to fetch fresh listings."
      />
    );
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
      {jobs.map((job, i) => (
        <div
          key={job.id}
          className={`animate-fade-in-up stagger-${Math.min(i + 1, 9)}`}
        >
          <JobCard job={job} onAction={onAction} />
        </div>
      ))}
    </div>
  );
}
