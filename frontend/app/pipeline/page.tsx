"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Kanban,
  Send,
  Mail,
  Users,
  Trophy,
  XCircle,
  AlertTriangle,
} from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { KanbanBoard } from "@/components/pipeline/KanbanBoard";
import {
  getPipelineApplications,
  advancePipelineStage,
  getPipelineCounts,
  getPipelineReminders,
} from "@/lib/api";
import type { PipelineApplication } from "@/lib/types";

// ---------------------------------------------------------------------------
// Stage meta (for stats row)
// ---------------------------------------------------------------------------

const STAGE_META = [
  {
    key: "applied",
    label: "Applied",
    icon: Send,
    color: "text-primary",
    bgColor: "bg-primary/10",
    borderColor: "border-primary/20",
    ringColor: "ring-primary/20",
  },
  {
    key: "outreach",
    label: "Outreach",
    icon: Mail,
    color: "text-blue-400",
    bgColor: "bg-blue-500/10",
    borderColor: "border-blue-500/20",
    ringColor: "ring-blue-500/20",
  },
  {
    key: "interview",
    label: "Interview",
    icon: Users,
    color: "text-amber-400",
    bgColor: "bg-amber-500/10",
    borderColor: "border-amber-500/20",
    ringColor: "ring-amber-500/20",
  },
  {
    key: "offer",
    label: "Offer",
    icon: Trophy,
    color: "text-emerald-400",
    bgColor: "bg-emerald-500/10",
    borderColor: "border-emerald-500/20",
    ringColor: "ring-emerald-500/20",
  },
  {
    key: "rejected",
    label: "Rejected",
    icon: XCircle,
    color: "text-rose-400",
    bgColor: "bg-rose-500/10",
    borderColor: "border-rose-500/20",
    ringColor: "ring-rose-500/20",
  },
] as const;

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function PipelinePage() {
  const [applications, setApplications] = useState<PipelineApplication[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [reminders, setReminders] = useState<PipelineApplication[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // ---- Fetch all data ----
  const fetchData = useCallback(async () => {
    try {
      setError(null);
      const [appsRes, countsRes, remindersRes] = await Promise.all([
        getPipelineApplications(),
        getPipelineCounts(),
        getPipelineReminders(),
      ]);
      setApplications(appsRes.applications);
      setCounts(countsRes);
      setReminders(
        (remindersRes.reminders ?? []) as PipelineApplication[]
      );
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load pipeline data"
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // ---- Advance handler ----
  async function handleAdvance(jobId: number, stage: string) {
    try {
      const updated = await advancePipelineStage(jobId, { stage });
      // Optimistic update: replace the app in local state
      setApplications((prev) =>
        prev.map((a) => (a.job_id === jobId ? { ...a, ...updated } : a))
      );
      // Refresh counts
      const newCounts = await getPipelineCounts();
      setCounts(newCounts);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to advance stage"
      );
    }
  }

  // ---- Total count ----
  const total = Object.values(counts).reduce((sum, n) => sum + n, 0);

  // ---- Loading skeleton ----
  if (loading) {
    return (
      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6">
        {/* Header skeleton */}
        <div className="mb-8">
          <Skeleton className="h-8 w-64 mb-2" />
          <Skeleton className="h-5 w-96" />
        </div>

        {/* Stats row skeleton */}
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3 mb-8">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-24 rounded-xl" />
          ))}
        </div>

        {/* Board skeleton */}
        <div className="flex gap-3 overflow-hidden">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="min-w-[250px] flex-1">
              <Skeleton className="h-12 rounded-t-xl mb-0" />
              <Skeleton className="h-64 rounded-b-xl" />
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="relative">
      <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="aurora-glow-top aurora-glow-animated" />
        <div className="aurora-glow-left" />
      </div>

      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6">
      {/* ═══════════════════════════════════════════════════
          HEADER
          ═══════════════════════════════════════════════════ */}
      <div className="animate-fade-in-up stagger-1 mb-8">
        <div className="flex items-center gap-3 mb-2">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 ring-1 ring-primary/20">
            <Kanban className="h-5 w-5 text-primary" />
          </div>
          <h1 className="font-heading text-2xl font-bold tracking-tight sm:text-3xl">
            <span className="text-gradient-lime">Application Pipeline</span>
          </h1>
        </div>
        <p className="text-muted-foreground text-sm sm:text-base ml-[52px]">
          Track every opportunity from application to offer.{" "}
          <span className="font-mono text-foreground/80">{total}</span>{" "}
          {total === 1 ? "application" : "applications"} in your pipeline.
        </p>
      </div>

      {/* ═══════════════════════════════════════════════════
          STATS ROW — count per stage
          ═══════════════════════════════════════════════════ */}
      <div className="animate-fade-in-up stagger-2 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3 mb-8">
        {STAGE_META.map((stage) => {
          const Icon = stage.icon;
          const count = counts[stage.key] ?? 0;
          return (
            <div
              key={stage.key}
              className={`glass-card rounded-xl p-4 flex items-center gap-3`}
            >
              <div
                className={`flex h-9 w-9 items-center justify-center rounded-lg ${stage.bgColor} ring-1 ${stage.ringColor} flex-shrink-0`}
              >
                <Icon className={`h-4 w-4 ${stage.color}`} />
              </div>
              <div>
                <p className="font-mono text-xl font-bold tracking-tight text-foreground">
                  {count}
                </p>
                <p className="text-xs text-muted-foreground">{stage.label}</p>
              </div>
            </div>
          );
        })}
      </div>

      {/* ═══════════════════════════════════════════════════
          REMINDERS ALERT
          ═══════════════════════════════════════════════════ */}
      {reminders.length > 0 && (
        <div className="animate-fade-in-up stagger-3 mb-6 rounded-xl border border-amber-500/20 bg-amber-500/[0.06] p-4">
          <div className="flex items-start gap-3">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-amber-500/15 ring-1 ring-amber-500/25 flex-shrink-0 mt-0.5">
              <AlertTriangle className="h-4 w-4 text-amber-400" />
            </div>
            <div>
              <h3 className="font-heading text-sm font-semibold text-amber-400">
                {reminders.length} application{reminders.length !== 1 ? "s" : ""}{" "}
                need attention
              </h3>
              <p className="text-xs text-muted-foreground mt-1">
                The following applications have had no progress for over 7 days:
              </p>
              <ul className="mt-2 space-y-1">
                {reminders.map((r) => (
                  <li
                    key={r.job_id}
                    className="flex items-center gap-2 text-xs text-foreground/80"
                  >
                    <span className="h-1 w-1 rounded-full bg-amber-400 flex-shrink-0" />
                    <span className="font-medium">
                      {r.title || `Job #${r.job_id}`}
                    </span>
                    {r.company && (
                      <span className="text-muted-foreground">
                        at {r.company}
                      </span>
                    )}
                    <span className="text-muted-foreground/60 ml-auto font-mono">
                      {r.stage}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      )}

      {/* ═══════════════════════════════════════════════════
          ERROR STATE
          ═══════════════════════════════════════════════════ */}
      {error && (
        <div className="mb-6 rounded-xl border border-destructive/20 bg-destructive/[0.06] p-4">
          <p className="text-sm text-destructive">{error}</p>
        </div>
      )}

      {/* ═══════════════════════════════════════════════════
          EMPTY STATE
          ═══════════════════════════════════════════════════ */}
      {total === 0 && !loading && !error && (
        <div className="animate-fade-in-up stagger-3 flex flex-col items-center justify-center py-20 text-center">
          <div className="rounded-full border-2 border-dashed border-primary/20 p-6 animate-pulse-glow mb-4">
            <Kanban className="h-12 w-12 text-primary/40" />
          </div>
          <h3 className="font-heading text-lg font-semibold text-foreground/80 mb-1">
            No applications yet
          </h3>
          <p className="text-sm text-muted-foreground max-w-sm">
            When you apply to jobs from the dashboard, they will appear here so
            you can track your progress through each stage.
          </p>
        </div>
      )}

      {/* ═══════════════════════════════════════════════════
          KANBAN BOARD
          ═══════════════════════════════════════════════════ */}
      {total > 0 && (
        <div className="animate-fade-in-up stagger-4">
          <KanbanBoard
            applications={applications}
            onAdvance={handleAdvance}
          />
        </div>
      )}
      </div>
    </div>
  );
}
