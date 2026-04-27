"use client";

import { SearchX } from "lucide-react";
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
