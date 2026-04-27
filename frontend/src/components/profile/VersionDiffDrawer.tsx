"use client";

import { useEffect, useState } from "react";
import { GitCompare } from "lucide-react";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import { getProfileVersionDiff } from "@/lib/api";
import type { ProfileVersionDiff } from "@/lib/types";

interface VersionDiffDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  v1: number;
  v2: number;
  v1Label: string;
  v2Label: string;
}

function formatFieldName(field: string): string {
  return field
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value)) {
    return value.length === 0 ? "(empty)" : value.map(String).join(", ");
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

export function VersionDiffDrawer({
  open,
  onOpenChange,
  v1,
  v2,
  v1Label,
  v2Label,
}: VersionDiffDrawerProps) {
  const [diff, setDiff] = useState<ProfileVersionDiff | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;

    async function fetchDiff() {
      setLoading(true);
      setError(null);
      setDiff(null);
      try {
        const data = await getProfileVersionDiff(v1, v2);
        if (!cancelled) setDiff(data);
      } catch (err: unknown) {
        if (!cancelled)
          setError(err instanceof Error ? err.message : "Failed to load diff");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    fetchDiff();
    return () => {
      cancelled = true;
    };
  }, [open, v1, v2]);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="flex w-full flex-col p-0 sm:w-[520px]">
        <SheetHeader className="border-b border-border p-4 pb-3">
          <div className="flex items-center gap-2">
            <GitCompare className="h-4 w-4 text-primary" />
            <SheetTitle>Compare Versions</SheetTitle>
          </div>
          <SheetDescription className="text-xs">
            <span className="font-medium text-foreground">{v1Label}</span>
            {" vs "}
            <span className="font-medium text-foreground">{v2Label}</span>
          </SheetDescription>
        </SheetHeader>

        <div className="flex-1 overflow-y-auto p-4">
          {loading && (
            <div className="flex flex-col gap-3">
              {Array.from({ length: 4 }).map((_, i) => (
                <div
                  key={i}
                  className="animate-pulse space-y-2 rounded-lg border border-border bg-muted/30 p-4"
                >
                  <div className="h-3.5 w-24 rounded bg-muted" />
                  <div className="h-3 w-full rounded bg-muted" />
                  <div className="h-3 w-3/4 rounded bg-muted" />
                </div>
              ))}
            </div>
          )}

          {!loading && error && (
            <div className="rounded-lg border border-destructive/20 bg-destructive/5 p-4 text-sm text-destructive">
              {error}
            </div>
          )}

          {!loading && !error && diff && diff.changed_fields.length === 0 && (
            <div className="flex flex-col items-center justify-center gap-2 py-12 text-center text-muted-foreground">
              <GitCompare className="h-8 w-8 opacity-30" />
              <p className="text-sm">No differences found between these versions.</p>
            </div>
          )}

          {!loading && !error && diff && diff.changed_fields.length > 0 && (
            <div className="flex flex-col gap-3">
              {diff.changed_fields.map((field) => {
                const change = diff.changes[field];
                return (
                  <div
                    key={field}
                    className="overflow-hidden rounded-lg border border-border bg-card/50"
                  >
                    <div className="border-b border-border bg-muted/40 px-3 py-2">
                      <p className="text-xs font-semibold text-foreground">
                        {formatFieldName(field)}
                      </p>
                    </div>
                    <div className="grid grid-cols-2 divide-x divide-border text-xs">
                      <div className="space-y-1 p-3">
                        <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                          Before
                        </p>
                        <p className="break-words whitespace-pre-wrap text-foreground/80">
                          {formatValue(change?.from)}
                        </p>
                      </div>
                      <div className="space-y-1 bg-primary/5 p-3">
                        <p className="text-[10px] font-medium uppercase tracking-wide text-primary">
                          After
                        </p>
                        <p className="break-words whitespace-pre-wrap text-foreground">
                          {formatValue(change?.to)}
                        </p>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
