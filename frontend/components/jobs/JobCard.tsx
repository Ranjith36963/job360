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
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
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
    >
      {/* ---- Top row: Score + Title ---- */}
      <div className="flex items-start gap-3">
        {/* Score badge */}
        <div
          className={`score-badge ${scoreClass(job.match_score)} flex-shrink-0 flex items-center justify-center rounded-lg w-11 h-11 text-lg font-bold`}
        >
          {job.match_score}
        </div>

        <div className="min-w-0 flex-1">
          <h3 className="font-heading font-semibold text-foreground leading-tight line-clamp-2">
            {job.title}
          </h3>
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 mt-1 text-sm text-muted-foreground">
            <span className="flex items-center gap-1">
              <Briefcase className="h-3 w-3 flex-shrink-0" />
              <span className="truncate max-w-[120px]">{job.company}</span>
            </span>
            <span className="hidden sm:inline text-border">|</span>
            <span className="flex items-center gap-1">
              <MapPin className="h-3 w-3 flex-shrink-0" />
              <span className="truncate max-w-[120px]">{job.location || "Remote"}</span>
            </span>
            <span className="hidden sm:inline text-border">|</span>
            <span className="flex items-center gap-1">
              <Clock className="h-3 w-3 flex-shrink-0" />
              {timeAgo(job.date_found)}
            </span>
          </div>
        </div>
      </div>

      {/* ---- Salary + type + visa ---- */}
      <div className="flex flex-wrap items-center gap-2">
        {job.salary && (
          <span className="flex items-center gap-1 text-sm text-foreground/80">
            <PoundSterling className="h-3 w-3" />
            {job.salary}
          </span>
        )}
        {job.job_type && (
          <Badge variant="secondary" className="text-xs">
            {job.job_type}
          </Badge>
        )}
        {job.visa_flag && (
          <Tooltip>
            <TooltipTrigger
              render={
                <Badge variant="outline" className="text-xs gap-1 border-primary/30 text-primary" />
              }
            >
              <Shield className="h-3 w-3" />
              Visa
            </TooltipTrigger>
            <TooltipContent>Visa sponsorship mentioned</TooltipContent>
          </Tooltip>
        )}
        {job.experience_level && (
          <Badge variant="secondary" className="text-xs">
            {job.experience_level}
          </Badge>
        )}
      </div>

      {/* ---- Skills ---- */}
      <div className="flex flex-wrap gap-1.5">
        {job.matched_skills.slice(0, 6).map((skill) => (
          <span
            key={skill}
            className="skill-matched inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs"
          >
            <Check className="h-3 w-3" />
            {skill}
          </span>
        ))}
        {job.missing_required.slice(0, 3).map((skill) => (
          <span
            key={skill}
            className="skill-missing inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs"
          >
            <X className="h-3 w-3" />
            {skill}
          </span>
        ))}
        {job.transferable_skills.slice(0, 2).map((skill) => (
          <span
            key={skill}
            className="skill-transferable inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs"
          >
            ~{skill}
          </span>
        ))}
      </div>

      {/* ---- Actions ---- */}
      <div className="flex items-center gap-2 pt-1 border-t border-border/50">
        <a
          href={job.apply_url}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(e) => e.stopPropagation()}
        >
          <Button
            size="sm"
            className="gap-1.5 bg-primary/10 text-primary hover:bg-primary/20 border border-primary/20"
          >
            <ExternalLink className="h-3.5 w-3.5" />
            Apply
          </Button>
        </a>

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
        >
          <Heart className={`h-3.5 w-3.5 ${isLiked ? "fill-current" : ""}`} />
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
        >
          <X className="h-3.5 w-3.5" />
          {isSkipped ? "Skipped" : "Skip"}
        </Button>

        {/* Source tag pushed to the right */}
        <span className="ml-auto text-xs text-muted-foreground/60 font-mono">
          {job.source}
        </span>
      </div>
    </div>
  );
}
