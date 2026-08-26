"use client";

import { useEffect, useRef } from "react";

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
  // Keep the SELECTED bucket on screen: this row scrolls with no visible
  // scrollbar, and the default bucket is the last one, so on a phone the active
  // filter opened off-screen with no hint it existed.
  // Feature-detected because jsdom has no layout and no scrollIntoView; calling
  // it unguarded threw during mount and took the whole page down.
  // Numbers: tests/design/README.md.
  const activeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const el = activeRef.current;
    if (typeof el?.scrollIntoView !== "function") return;
    el.scrollIntoView({ inline: "center", block: "nearest", behavior: "smooth" });
  }, [activeBucket]);

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
            ref={isActive ? activeRef : undefined}
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
