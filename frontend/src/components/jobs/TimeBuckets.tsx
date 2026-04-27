"use client";

import { cn } from "@/lib/utils";

interface TimeBucketsProps {
  activeBucket: string;
  onBucketChange: (bucket: string) => void;
  counts: Record<string, number>;
}

const BUCKETS = [
  { key: "all", label: "All" },
  { key: "24h", label: "24h" },
  { key: "48h", label: "48h" },
  { key: "3d", label: "3d" },
  { key: "5d", label: "5d" },
  { key: "7d", label: "7d" },
] as const;

export function TimeBuckets({
  activeBucket,
  onBucketChange,
  counts,
}: TimeBucketsProps) {
  return (
    <div
      className="flex items-center gap-1 overflow-x-auto pb-1 scrollbar-none"
      role="group"
      aria-label="Filter jobs by time range"
    >
      {BUCKETS.map(({ key, label }) => {
        const isActive = activeBucket === key;
        const count = counts[key] ?? 0;

        return (
          <button
            key={key}
            onClick={() => onBucketChange(key)}
            aria-pressed={isActive}
            aria-label={`${label} — ${count} job${count !== 1 ? "s" : ""}`}
            className={cn(
              "relative flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm font-medium transition-all whitespace-nowrap select-none",
              isActive
                ? "bg-primary/10 text-primary border border-primary/30"
                : "text-muted-foreground hover:text-foreground hover:bg-muted/50 border border-transparent"
            )}
          >
            {label}
            <span
              aria-hidden="true"
              className={cn(
                "inline-flex items-center justify-center rounded-full px-1.5 min-w-[20px] h-5 text-xs font-mono tabular-nums",
                isActive
                  ? "bg-primary/20 text-primary"
                  : "bg-muted text-muted-foreground"
              )}
            >
              {count}
            </span>
          </button>
        );
      })}
    </div>
  );
}
