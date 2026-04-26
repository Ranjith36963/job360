"use client";
// NOTE: @dnd-kit/core DnD is deferred — install with: npm install @dnd-kit/core @dnd-kit/sortable

import { useState, useCallback } from "react";
import {
  Send,
  Mail,
  Users,
  Trophy,
  XCircle,
  ChevronRight,
  Clock,
  AlertTriangle,
  ArrowRight,
  History,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { NotesEditor } from "./NotesEditor";
import { PipelineFilterPanel } from "./PipelineFilterPanel";
import { getApplicationTimeline } from "@/lib/api";
import type {
  PipelineApplication,
  TimelineEntry,
} from "@/lib/types";

// ---------------------------------------------------------------------------
// Stage definitions
// ---------------------------------------------------------------------------

const STAGES = [
  {
    key: "applied",
    label: "Applied",
    icon: Send,
    color: "text-primary",
    bgColor: "bg-primary/10",
    borderColor: "border-primary/20",
    ringColor: "ring-primary/20",
    headerBg: "bg-primary/[0.06]",
    badgeBg: "bg-primary/15 text-primary",
  },
  {
    key: "outreach",
    label: "Outreach",
    icon: Mail,
    color: "text-blue-400",
    bgColor: "bg-blue-500/10",
    borderColor: "border-blue-500/20",
    ringColor: "ring-blue-500/20",
    headerBg: "bg-blue-500/[0.06]",
    badgeBg: "bg-blue-500/15 text-blue-400",
  },
  {
    key: "interview",
    label: "Interview",
    icon: Users,
    color: "text-amber-400",
    bgColor: "bg-amber-500/10",
    borderColor: "border-amber-500/20",
    ringColor: "ring-amber-500/20",
    headerBg: "bg-amber-500/[0.06]",
    badgeBg: "bg-amber-500/15 text-amber-400",
  },
  {
    key: "offer",
    label: "Offer",
    icon: Trophy,
    color: "text-emerald-400",
    bgColor: "bg-emerald-500/10",
    borderColor: "border-emerald-500/20",
    ringColor: "ring-emerald-500/20",
    headerBg: "bg-emerald-500/[0.06]",
    badgeBg: "bg-emerald-500/15 text-emerald-400",
  },
  {
    key: "rejected",
    label: "Rejected",
    icon: XCircle,
    color: "text-rose-400",
    bgColor: "bg-rose-500/10",
    borderColor: "border-rose-500/20",
    ringColor: "ring-rose-500/20",
    headerBg: "bg-rose-500/[0.06]",
    badgeBg: "bg-rose-500/15 text-rose-400",
  },
] as const;

const STAGE_ORDER: string[] = STAGES.map((s) => s.key);

function nextStage(current: string): string | null {
  const idx = STAGE_ORDER.indexOf(current);
  // No advance from offer or rejected
  if (idx === -1 || current === "offer" || current === "rejected") return null;
  return STAGE_ORDER[idx + 1] ?? null;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function daysAgo(dateStr: string): number {
  const diff = Date.now() - new Date(dateStr).getTime();
  return Math.floor(diff / 86_400_000);
}

function daysAgoLabel(dateStr: string): string {
  const d = daysAgo(dateStr);
  if (d === 0) return "Today";
  if (d === 1) return "1 day ago";
  return `${d} days ago`;
}

function isOverdue(app: PipelineApplication): boolean {
  return daysAgo(app.updated_at) > 7;
}

const DATE_FMT = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

// ---------------------------------------------------------------------------
// Application card
// ---------------------------------------------------------------------------

function ApplicationCard({
  app,
  stage,
  onAdvance,
  onRequestConfirm,
  onOpenTimeline,
  onNotesaved,
}: {
  app: PipelineApplication;
  stage: (typeof STAGES)[number];
  onAdvance: (jobId: number, nextStage: string) => void;
  onRequestConfirm: (jobId: number, targetStage: string) => void;
  onOpenTimeline: (jobId: number) => void;
  onNotesaved: () => void;
}) {
  const next = nextStage(app.stage);
  const overdue = isOverdue(app);

  function handleAdvanceClick() {
    if (!next) return;
    // C-09: require confirmation when advancing to "rejected"
    if (next === "rejected") {
      onRequestConfirm(app.job_id, next);
    } else {
      onAdvance(app.job_id, next);
    }
  }

  return (
    <div className="glass-card rounded-lg p-3 flex flex-col gap-2">
      {/* Title + overdue indicator */}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <h4 className="font-heading font-medium text-sm text-foreground leading-tight line-clamp-2">
            {app.title || `Job #${app.job_id}`}
          </h4>
          {app.company && (
            <p className="text-xs text-muted-foreground mt-0.5 truncate">
              {app.company}
            </p>
          )}
        </div>
        {overdue && (
          <Tooltip>
            <TooltipTrigger
              render={
                <span className="flex-shrink-0 flex items-center justify-center h-5 w-5 rounded-md bg-amber-500/15" />
              }
            >
              <AlertTriangle className="h-3 w-3 text-amber-400" />
            </TooltipTrigger>
            <TooltipContent>
              No progress for {daysAgo(app.updated_at)} days
            </TooltipContent>
          </Tooltip>
        )}
      </div>

      {/* Time info */}
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Clock className="h-3 w-3 flex-shrink-0" />
        <span>Applied {daysAgoLabel(app.created_at)}</span>
      </div>

      {/* Notes preview */}
      {app.notes && (
        <p className="text-xs text-muted-foreground/80 line-clamp-2 italic">
          {app.notes}
        </p>
      )}

      {/* Action row: notes editor + history + advance */}
      <div className="flex items-center gap-1 mt-1">
        {/* C-08: NotesEditor */}
        <NotesEditor
          jobId={app.job_id}
          initialNotes={app.notes}
          onSaved={onNotesaved}
        />

        {/* C-11: History / timeline button */}
        <Button
          size="sm"
          variant="ghost"
          className="h-7 gap-1 text-xs text-muted-foreground hover:text-foreground px-2"
          onClick={() => onOpenTimeline(app.job_id)}
        >
          <History className="h-3 w-3" />
          History
        </Button>

        {/* Advance button — push to right */}
        {next && (
          <Button
            size="sm"
            className={`ml-auto gap-1.5 text-xs h-7 ${stage.bgColor} ${stage.color} hover:brightness-125 border ${stage.borderColor}`}
            onClick={handleAdvanceClick}
          >
            Advance
            <ArrowRight className="h-3 w-3" />
          </Button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Kanban column
// ---------------------------------------------------------------------------

function KanbanColumn({
  stage,
  applications,
  onAdvance,
  onRequestConfirm,
  onOpenTimeline,
  onNotesSaved,
  stagger,
}: {
  stage: (typeof STAGES)[number];
  applications: PipelineApplication[];
  onAdvance: (jobId: number, nextStage: string) => void;
  onRequestConfirm: (jobId: number, targetStage: string) => void;
  onOpenTimeline: (jobId: number) => void;
  onNotesSaved: () => void;
  stagger: number;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const Icon = stage.icon;
  const count = applications.length;

  return (
    <div
      className={`animate-fade-in-up stagger-${stagger} flex flex-col min-w-[250px] md:min-w-[270px]`}
    >
      {/* Column header */}
      <button
        onClick={() => setCollapsed((prev) => !prev)}
        className={`flex items-center justify-between gap-2 rounded-t-xl px-4 py-3 ${stage.headerBg} border ${stage.borderColor} border-b-0 md:cursor-default`}
      >
        <div className="flex items-center gap-2">
          <div
            className={`flex h-7 w-7 items-center justify-center rounded-md ${stage.bgColor} ring-1 ${stage.ringColor}`}
          >
            <Icon className={`h-3.5 w-3.5 ${stage.color}`} />
          </div>
          <span className={`text-sm font-semibold ${stage.color}`}>
            {stage.label}
          </span>
        </div>
        <Badge
          variant="secondary"
          className={`font-mono text-xs px-2 ${stage.badgeBg}`}
        >
          {count}
        </Badge>
        {/* Chevron indicator for mobile */}
        <ChevronRight
          className={`h-4 w-4 text-muted-foreground transition-transform md:hidden ${
            collapsed ? "" : "rotate-90"
          }`}
        />
      </button>

      {/* Column body */}
      <div
        className={`flex-1 rounded-b-xl border ${stage.borderColor} border-t-0 bg-card/30 ${
          collapsed ? "hidden md:flex" : "flex"
        } flex-col gap-2 p-2 overflow-y-auto max-h-[500px] md:max-h-[calc(100vh-320px)]`}
      >
        {applications.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-8 text-center">
            <Icon className="h-8 w-8 text-muted-foreground/20 mb-2" />
            <p className="text-xs text-muted-foreground/50">
              No applications
            </p>
          </div>
        ) : (
          applications.map((app) => (
            <ApplicationCard
              key={app.job_id}
              app={app}
              stage={stage}
              onAdvance={onAdvance}
              onRequestConfirm={onRequestConfirm}
              onOpenTimeline={onOpenTimeline}
              onNotesaved={onNotesSaved}
            />
          ))
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// KanbanBoard (exported)
// ---------------------------------------------------------------------------

interface KanbanBoardProps {
  applications: PipelineApplication[];
  onAdvance: (jobId: number, stage: string) => void;
  onRefresh?: () => void;
}

export function KanbanBoard({ applications, onAdvance, onRefresh }: KanbanBoardProps) {
  // C-10: filtered subset
  const [filteredApps, setFilteredApps] = useState<PipelineApplication[]>(applications);

  // C-09: confirmation dialog state
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [pendingAdvance, setPendingAdvance] = useState<{
    jobId: number;
    stage: string;
  } | null>(null);

  // C-11: timeline sheet state
  const [timelineOpen, setTimelineOpen] = useState(false);
  const [timelineJobId, setTimelineJobId] = useState<number | null>(null);
  const [timelineEntries, setTimelineEntries] = useState<TimelineEntry[]>([]);
  const [timelineLoading, setTimelineLoading] = useState(false);
  const [timelineError, setTimelineError] = useState<string | null>(null);

  // Keep filteredApps in sync when applications prop changes (e.g. after refresh)
  // The PipelineFilterPanel's useEffect will re-run and call setFilteredApps.

  // C-11: open timeline drawer and fetch entries
  const handleOpenTimeline = useCallback(async (jobId: number) => {
    setTimelineJobId(jobId);
    setTimelineOpen(true);
    setTimelineLoading(true);
    setTimelineError(null);
    setTimelineEntries([]);
    try {
      const res = await getApplicationTimeline(jobId);
      setTimelineEntries(res.entries);
    } catch (err) {
      setTimelineError(
        err instanceof Error ? err.message : "Failed to load timeline"
      );
    } finally {
      setTimelineLoading(false);
    }
  }, []);

  // C-09: request confirmation before advancing to rejected
  function handleRequestConfirm(jobId: number, targetStage: string) {
    setPendingAdvance({ jobId, stage: targetStage });
    setConfirmOpen(true);
  }

  function handleConfirmAdvance() {
    if (pendingAdvance) {
      onAdvance(pendingAdvance.jobId, pendingAdvance.stage);
    }
    setConfirmOpen(false);
    setPendingAdvance(null);
  }

  function handleCancelConfirm() {
    setConfirmOpen(false);
    setPendingAdvance(null);
  }

  // Refresh callback for NotesEditor
  const handleNotesSaved = useCallback(() => {
    onRefresh?.();
  }, [onRefresh]);

  // Group filtered applications by stage
  const grouped = STAGES.reduce(
    (acc, stage) => {
      acc[stage.key] = filteredApps.filter((a) => a.stage === stage.key);
      return acc;
    },
    {} as Record<string, PipelineApplication[]>
  );

  return (
    <>
      {/* C-10: Filter panel */}
      <PipelineFilterPanel
        applications={applications}
        onFilter={setFilteredApps}
      />

      {/* Desktop: horizontal scroll row */}
      <div className="hidden md:flex gap-3 overflow-x-auto pb-4 snap-x">
        {STAGES.map((stage, i) => (
          <KanbanColumn
            key={stage.key}
            stage={stage}
            applications={grouped[stage.key] || []}
            onAdvance={onAdvance}
            onRequestConfirm={handleRequestConfirm}
            onOpenTimeline={handleOpenTimeline}
            onNotesSaved={handleNotesSaved}
            stagger={i + 1}
          />
        ))}
      </div>

      {/* Mobile: vertical stack with collapsible sections */}
      <div className="flex flex-col gap-3 md:hidden">
        {STAGES.map((stage, i) => (
          <KanbanColumn
            key={stage.key}
            stage={stage}
            applications={grouped[stage.key] || []}
            onAdvance={onAdvance}
            onRequestConfirm={handleRequestConfirm}
            onOpenTimeline={handleOpenTimeline}
            onNotesSaved={handleNotesSaved}
            stagger={i + 1}
          />
        ))}
      </div>

      {/* C-09: Confirmation dialog for advancing to "rejected" */}
      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent showCloseButton={false}>
          <DialogHeader>
            <DialogTitle>Move to Rejected?</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            This will move the application to <strong>Rejected</strong>. This
            cannot be undone.
          </p>
          <DialogFooter>
            <Button variant="outline" size="sm" onClick={handleCancelConfirm}>
              Cancel
            </Button>
            <Button
              size="sm"
              className="bg-rose-500/15 text-rose-400 hover:brightness-125 border border-rose-500/20"
              onClick={handleConfirmAdvance}
            >
              Confirm
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* C-11: Timeline sheet drawer */}
      <Sheet open={timelineOpen} onOpenChange={setTimelineOpen}>
        <SheetContent side="right">
          <SheetHeader>
            <SheetTitle>
              Application History
              {timelineJobId !== null && (
                <span className="ml-2 font-mono text-xs text-muted-foreground font-normal">
                  #{timelineJobId}
                </span>
              )}
            </SheetTitle>
          </SheetHeader>

          <div className="flex-1 overflow-y-auto px-4 pb-4">
            {timelineLoading && (
              <p className="text-sm text-muted-foreground mt-4">Loading…</p>
            )}

            {timelineError && (
              <p className="text-sm text-destructive mt-4">{timelineError}</p>
            )}

            {!timelineLoading && !timelineError && timelineEntries.length === 0 && (
              <p className="text-sm text-muted-foreground mt-4">
                No timeline entries found.
              </p>
            )}

            {!timelineLoading && !timelineError && timelineEntries.length > 0 && (
              <ol className="relative mt-4 border-l border-border/40 pl-4 space-y-4">
                {timelineEntries.map((entry) => (
                  <li key={entry.id} className="relative">
                    {/* Timeline dot */}
                    <span className="absolute -left-[21px] top-1 h-2.5 w-2.5 rounded-full bg-primary/40 ring-2 ring-background" />

                    <p className="text-xs font-medium text-foreground">
                      {entry.from_stage ? (
                        <>
                          <span className="text-muted-foreground">
                            {entry.from_stage}
                          </span>
                          {" → "}
                          <span>{entry.to_stage}</span>
                        </>
                      ) : (
                        <span>{entry.to_stage}</span>
                      )}
                    </p>

                    <p className="text-xs text-muted-foreground mt-0.5">
                      {DATE_FMT.format(new Date(entry.transitioned_at))}
                    </p>

                    {entry.notes && (
                      <p className="mt-1 text-xs text-muted-foreground/80 italic">
                        {entry.notes}
                      </p>
                    )}
                  </li>
                ))}
              </ol>
            )}
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}
