"use client";

import { useEffect, useState } from "react";
import { ExternalLink, Layers } from "lucide-react";
import { getJobDuplicates } from "@/lib/api";
import type { DuplicateJobsResponse } from "@/lib/types";

interface DedupGroupViewerProps {
  jobId: number;
}

export function DedupGroupViewer({ jobId }: DedupGroupViewerProps) {
  const [data, setData] = useState<DuplicateJobsResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    getJobDuplicates(jobId)
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch(() => {
        // silently ignore — dedup data is best-effort
      });
    return () => {
      cancelled = true;
    };
  }, [jobId]);

  if (!data || data.total === 0) return null;

  return (
    <div className="rounded-xl border border-border/60 bg-muted/20 p-4">
      <div className="mb-3 flex items-center gap-2">
        <Layers className="h-4 w-4 text-primary/70" aria-hidden="true" />
        <p className="text-sm font-medium text-foreground">
          Also posted on{" "}
          <span className="text-primary">
            {data.total} other source{data.total !== 1 ? "s" : ""}
          </span>
        </p>
      </div>
      <ul className="flex flex-col gap-2">
        {data.duplicates.map((dup) => (
          <li
            key={dup.id}
            className="flex items-center justify-between gap-3 rounded-lg bg-background/60 px-3 py-2 text-sm"
          >
            <div className="min-w-0 flex-1">
              <span className="mr-2 inline-block rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                {dup.source}
              </span>
              <span className="truncate text-foreground/80">{dup.title}</span>
              <span className="text-muted-foreground"> · {dup.company}</span>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <span className="text-xs font-medium text-primary">{dup.match_score}%</span>
              <a
                href={dup.apply_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-muted-foreground hover:text-primary transition-colors"
                aria-label={`Apply via ${dup.source} for ${dup.title}`}
              >
                <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
              </a>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
