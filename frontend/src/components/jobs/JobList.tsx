"use client";

import Link from "next/link";
import { FileUp, SearchX } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
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
   * Does this account have a profile yet? (wiring.md W-03)
   *
   * THREE states, deliberately — `undefined` means "we don't know yet". An empty
   * list has more than one cause, and telling a returning user to upload a CV he
   * already uploaded is as wrong as telling a new user to adjust filters he never
   * set. So the onboarding prompt renders ONLY on a definite `false`; while the
   * profile query is still in flight we keep the neutral message.
   */
  hasProfile?: boolean;
}

export function JobList({ jobs, loading, onAction, hasProfile }: JobListProps) {
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
    // W-03: a brand-new account has no filters to adjust and no search to widen —
    // the list is empty because we don't know anything about them yet. Say that,
    // and give them the one door that moves them forward.
    if (hasProfile === false) {
      return (
        <EmptyState
          icon={<FileUp className="h-8 w-8" />}
          title="Let's find your jobs"
          description="Upload your CV and we'll start matching UK roles to your actual experience. It takes about a minute."
          action={
            <Button render={<Link href="/profile" />}>Upload your CV</Button>
          }
        />
      );
    }

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
