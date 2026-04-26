"use client";

import { useRouter } from "next/navigation";
import {
  Heart,
  X,
  ExternalLink,
  Briefcase,
  MapPin,
  Clock,
  PoundSterling,
  Shield,
  Check,
  AlertTriangle,
  Eye,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
import { createPipelineApplication } from "@/lib/api";
import { toast } from "@/lib/toast";
import type { JobResponse } from "@/lib/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function scoreClass(score: number): string {
  if (score >= 70) return "score-high";
  if (score >= 50) return "score-good";
  if (score >= 30) return "score-mid";
  return "score-low";
}

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const hours = Math.floor(diff / 3_600_000);
  if (hours < 1) return "just now";
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days === 1) return "1d ago";
  if (days < 7) return `${days}d ago`;
  const weeks = Math.floor(days / 7);
  return weeks === 1 ? "1w ago" : `${weeks}w ago`;
}

function formatSalaryRange(min?: number | null, max?: number | null): string | null {
  if (!min && !max) return null;
  const fmt = (n: number) =>
    n >= 1000 ? `£${Math.round(n / 1000)}k` : `£${n}`;
  if (min && max) return `${fmt(min)}–${fmt(max)}`;
  if (min) return `${fmt(min)}+`;
  if (max) return `up to ${fmt(max)}`;
  return null;
}

function stalenessColor(state?: string | null): string {
  switch (state) {
    case "ACTIVE": return "text-green-400 border-green-400/30";
    case "STALE": return "text-yellow-400 border-yellow-400/30";
    case "GHOST": return "text-orange-400 border-orange-400/30";
    case "CONFIRMED_EXPIRED": return "text-red-400 border-red-400/30";
    default: return "text-muted-foreground border-border";
  }
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface JobCardProps {
  job: JobResponse;
  onAction: (jobId: number, action: string) => void;
}

export function JobCard({ job, onAction }: JobCardProps) {
  const router = useRouter();

  const isLiked = job.action === "liked";
  const isSkipped = job.action === "not_interested";

  const salaryRange = formatSalaryRange(job.salary_min_gbp, job.salary_max_gbp);
  const displayTime = job.last_seen_at ?? job.first_seen_at ?? job.date_found;
  const hasStalenessBadge =
    job.staleness_state &&
    job.staleness_state !== "ACTIVE" &&
    job.staleness_state !== "UNKNOWN";

  async function handleApply(e: React.MouseEvent) {
    e.stopPropagation();
    // Open apply URL and track in pipeline
    window.open(job.apply_url, "_blank", "noopener,noreferrer");
    try {
      await createPipelineApplication(job.id);
      toast.success("Added to pipeline — Applied");
    } catch (err) {
      // Not a blocking error — job still opened
      toast.apiError(err, "Added to pipeline tracking failed");
    }
  }

  function handleCardClick(e: React.MouseEvent) {
    // Don't navigate when clicking buttons or links
    const target = e.target as HTMLElement;
    if (target.closest("button") || target.closest("a")) return;
    router.push(`/jobs/${job.id}`);
  }

  return (
    <div
      onClick={handleCardClick}
      className="glass-card rounded-xl p-4 cursor-pointer flex flex-col gap-3"
      role="article"
      aria-label={`Job: ${job.title} at ${job.company}`}
    >
      {/* ---- Top row: Score + Title ---- */}
      <div className="flex items-start gap-3">
        {/* Score badge */}
        <div
          className={`score-badge ${scoreClass(job.match_score)} flex-shrink-0 flex items-center justify-center rounded-lg w-11 h-11 text-lg font-bold`}
          aria-label={`Match score: ${job.match_score}`}
        >
          {job.match_score}
        </div>

        <div className="min-w-0 flex-1">
          <h3 className="font-heading font-semibold text-foreground leading-tight line-clamp-2">
            {job.title}
          </h3>
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 mt-1 text-sm text-muted-foreground">
            <span className="flex items-center gap-1">
              <Briefcase className="h-3 w-3 flex-shrink-0" aria-hidden="true" />
              <span className="truncate max-w-[120px]">{job.company}</span>
            </span>
            <span className="hidden sm:inline text-border" aria-hidden="true">|</span>
            <span className="flex items-center gap-1">
              <MapPin className="h-3 w-3 flex-shrink-0" aria-hidden="true" />
              <span className="truncate max-w-[120px]">{job.location || "Remote"}</span>
            </span>
            <span className="hidden sm:inline text-border" aria-hidden="true">|</span>
            <span className="flex items-center gap-1">
              <Clock className="h-3 w-3 flex-shrink-0" aria-hidden="true" />
              {timeAgo(displayTime)}
            </span>
          </div>
        </div>

        {/* Staleness badge */}
        {hasStalenessBadge && (
          <Tooltip>
            <TooltipTrigger
              render={
                <Badge
                  variant="outline"
                  className={`flex-shrink-0 text-xs gap-1 ${stalenessColor(job.staleness_state)}`}
                />
              }
            >
              <AlertTriangle className="h-3 w-3" aria-hidden="true" />
              {job.staleness_state === "CONFIRMED_EXPIRED" ? "Expired" : job.staleness_state}
            </TooltipTrigger>
            <TooltipContent>
              {job.staleness_state === "CONFIRMED_EXPIRED"
                ? "This listing has been confirmed expired"
                : job.staleness_state === "GHOST"
                ? "Job listing appears to be gone"
                : "Listing may be stale — check before applying"}
            </TooltipContent>
          </Tooltip>
        )}
      </div>

      {/* ---- Salary + type + seniority + workplace + visa ---- */}
      <div className="flex flex-wrap items-center gap-2">
        {/* Structured salary range (preferred) */}
        {salaryRange && (
          <span className="flex items-center gap-1 text-sm text-foreground/80">
            <PoundSterling className="h-3 w-3" aria-hidden="true" />
            {salaryRange}
          </span>
        )}
        {/* Fallback: legacy salary string */}
        {!salaryRange && job.salary && (
          <span className="flex items-center gap-1 text-sm text-foreground/80">
            <PoundSterling className="h-3 w-3" aria-hidden="true" />
            {job.salary}
          </span>
        )}

        {/* Seniority pill */}
        {job.seniority && (
          <Badge variant="secondary" className="text-xs capitalize">
            {job.seniority.replace(/_/g, " ")}
          </Badge>
        )}

        {/* Workplace type pill */}
        {job.workplace_type && (
          <Badge variant="outline" className="text-xs capitalize">
            {job.workplace_type.replace(/_/g, " ")}
          </Badge>
        )}

        {/* Legacy job_type */}
        {job.job_type && !job.workplace_type && (
          <Badge variant="secondary" className="text-xs">
            {job.job_type}
          </Badge>
        )}

        {/* Visa sponsorship */}
        {(job.visa_sponsorship === true || job.visa_flag) && (
          <Tooltip>
            <TooltipTrigger
              render={
                <Badge
                  variant="outline"
                  className="text-xs gap-1 border-primary/30 text-primary"
                />
              }
            >
              <Shield className="h-3 w-3" aria-hidden="true" />
              Visa
            </TooltipTrigger>
            <TooltipContent>Visa sponsorship available</TooltipContent>
          </Tooltip>
        )}

        {/* Industry */}
        {job.industry && (
          <Badge variant="secondary" className="text-xs text-muted-foreground">
            {job.industry}
          </Badge>
        )}
      </div>

      {/* ---- Skills ---- */}
      <div className="flex flex-wrap gap-1.5" role="list" aria-label="Skills">
        {(job.matched_skills ?? []).slice(0, 6).map((skill) => (
          <span
            key={skill}
            className="skill-matched inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs"
            role="listitem"
          >
            <Check className="h-3 w-3" aria-hidden="true" />
            {skill}
          </span>
        ))}
        {(job.missing_required ?? []).slice(0, 3).map((skill) => (
          <span
            key={skill}
            className="skill-missing inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs"
            role="listitem"
          >
            <X className="h-3 w-3" aria-hidden="true" />
            {skill}
          </span>
        ))}
        {(job.transferable_skills ?? []).slice(0, 2).map((skill) => (
          <span
            key={skill}
            className="skill-transferable inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs"
            role="listitem"
          >
            ~{skill}
          </span>
        ))}
      </div>

      {/* ---- Actions ---- */}
      <div className="flex items-center gap-2 pt-1 border-t border-border/50">
        <Button
          size="sm"
          className="gap-1.5 bg-primary/10 text-primary hover:bg-primary/20 border border-primary/20"
          onClick={handleApply}
          aria-label={`Apply for ${job.title} at ${job.company}`}
        >
          <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
          Apply
        </Button>

        <Button
          size="sm"
          variant={isLiked ? "default" : "ghost"}
          className={
            isLiked
              ? "gap-1.5 bg-pink-500/15 text-pink-400 hover:bg-pink-500/25 border border-pink-500/20"
              : "gap-1.5 text-muted-foreground hover:text-pink-400"
          }
          onClick={(e) => {
            e.stopPropagation();
            onAction(job.id, isLiked ? "remove" : "liked");
          }}
          aria-label={isLiked ? "Unlike this job" : "Like this job"}
          aria-pressed={isLiked}
        >
          <Heart className={`h-3.5 w-3.5 ${isLiked ? "fill-current" : ""}`} aria-hidden="true" />
          {isLiked ? "Liked" : "Like"}
        </Button>

        <Button
          size="sm"
          variant={isSkipped ? "default" : "ghost"}
          className={
            isSkipped
              ? "gap-1.5 bg-destructive/15 text-destructive hover:bg-destructive/25 border border-destructive/20"
              : "gap-1.5 text-muted-foreground hover:text-destructive"
          }
          onClick={(e) => {
            e.stopPropagation();
            onAction(job.id, isSkipped ? "remove" : "not_interested");
          }}
          aria-label={isSkipped ? "Undo skip" : "Skip this job"}
          aria-pressed={isSkipped}
        >
          <X className="h-3.5 w-3.5" aria-hidden="true" />
          {isSkipped ? "Skipped" : "Skip"}
        </Button>

        {/* View job detail */}
        <Button
          size="sm"
          variant="ghost"
          className="gap-1.5 text-muted-foreground hover:text-foreground ml-auto"
          onClick={(e) => {
            e.stopPropagation();
            router.push(`/jobs/${job.id}`);
          }}
          aria-label={`View details for ${job.title}`}
        >
          <Eye className="h-3.5 w-3.5" aria-hidden="true" />
          <span className="hidden sm:inline">Details</span>
        </Button>

        {/* Source tag */}
        <span className="text-xs text-muted-foreground/60 font-mono">
          {job.source}
        </span>
      </div>
    </div>
  );
}
