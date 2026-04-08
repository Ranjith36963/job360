"use client";

import { SearchX } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
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
}

export function JobList({ jobs, loading, onAction }: JobListProps) {
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

  if (jobs.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-center">
        <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-muted mb-4">
          <SearchX className="h-8 w-8 text-muted-foreground" />
        </div>
        <h3 className="font-heading text-lg font-semibold text-foreground mb-1">
          No jobs found
        </h3>
        <p className="text-sm text-muted-foreground max-w-sm">
          Try adjusting your filters, expanding the time range, or lowering the
          minimum score. You can also run a new search to fetch fresh listings.
        </p>
      </div>
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
