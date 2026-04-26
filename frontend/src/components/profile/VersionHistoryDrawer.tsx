"use client";

import { useEffect, useState } from "react";
import { History, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import { getProfileVersions, restoreProfileVersion } from "@/lib/api";
import { toast } from "@/lib/toast";
import type { ProfileVersionSummary } from "@/lib/types";

interface VersionHistoryDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onRestore: () => void;
}

function formatDate(iso: string): string {
  try {
    return new Intl.DateTimeFormat("en-GB", {
      day: "numeric",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

function formatSourceAction(action: string): string {
  return action
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function VersionHistoryDrawer({
  open,
  onOpenChange,
  onRestore,
}: VersionHistoryDrawerProps) {
  const [versions, setVersions] = useState<ProfileVersionSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [restoringId, setRestoringId] = useState<number | null>(null);

  useEffect(() => {
    if (!open) return;

    let cancelled = false;

    async function fetchVersions() {
      setLoading(true);
      setError(null);
      try {
        const data = await getProfileVersions();
        if (!cancelled) {
          setVersions(data.versions);
        }
      } catch (err: unknown) {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "Failed to load version history"
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    fetchVersions();
    return () => {
      cancelled = true;
    };
  }, [open]);

  async function handleRestore(version: ProfileVersionSummary) {
    setRestoringId(version.id);
    try {
      await restoreProfileVersion(version.id);
      toast.success(`Restored profile from ${formatDate(version.created_at)}`);
      onRestore();
      onOpenChange(false);
    } catch (err: unknown) {
      toast.apiError(err, "Failed to restore profile version");
    } finally {
      setRestoringId(null);
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-80 sm:w-96 p-0 flex flex-col">
        <SheetHeader className="p-4 pb-3 border-b border-border">
          <div className="flex items-center gap-2">
            <History className="h-4 w-4 text-primary" />
            <SheetTitle>Version History</SheetTitle>
          </div>
          <SheetDescription>
            Restore your profile to a previous saved state.
          </SheetDescription>
        </SheetHeader>

        <div className="flex-1 overflow-y-auto">
          {loading && (
            <div className="flex flex-col gap-3 p-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <div
                  key={i}
                  className="animate-pulse rounded-lg border border-border bg-muted/30 p-4 space-y-2"
                >
                  <div className="h-3.5 w-32 rounded bg-muted" />
                  <div className="h-3 w-20 rounded bg-muted" />
                </div>
              ))}
            </div>
          )}

          {!loading && error && (
            <div className="p-4">
              <div className="rounded-lg border border-destructive/20 bg-destructive/5 p-4 text-sm text-destructive">
                {error}
              </div>
            </div>
          )}

          {!loading && !error && versions.length === 0 && (
            <div className="flex flex-col items-center justify-center gap-2 p-8 text-center text-muted-foreground">
              <History className="h-8 w-8 opacity-30" />
              <p className="text-sm">No version history yet.</p>
              <p className="text-xs">
                Each time you upload a CV or save preferences, a version is saved
                here.
              </p>
            </div>
          )}

          {!loading && !error && versions.length > 0 && (
            <ul className="flex flex-col gap-2 p-4">
              {versions.map((version, idx) => (
                <li
                  key={version.id}
                  className="flex items-start justify-between gap-3 rounded-lg border border-border bg-card/50 p-3 transition-colors hover:bg-muted/30"
                >
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-foreground truncate">
                      {formatSourceAction(version.source_action)}
                    </p>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      {formatDate(version.created_at)}
                    </p>
                    {idx === 0 && (
                      <span className="mt-1 inline-block rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
                        Current
                      </span>
                    )}
                  </div>
                  {idx !== 0 && (
                    <Button
                      variant="outline"
                      size="sm"
                      className="shrink-0 gap-1.5"
                      disabled={restoringId === version.id}
                      onClick={() => handleRestore(version)}
                    >
                      {restoringId === version.id ? (
                        <span className="animate-spin inline-block h-3 w-3 border-2 border-current border-t-transparent rounded-full" />
                      ) : (
                        <RotateCcw className="h-3 w-3" />
                      )}
                      Restore
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
