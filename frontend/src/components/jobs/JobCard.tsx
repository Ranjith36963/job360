"use client";

import { useRouter } from "next/navigation";
import posthog from "posthog-js";
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
  CalendarClock,
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
import { safeUrl } from "@/lib/utils";
import { scoreClass } from "@/lib/scoring";
import { stalenessBadge } from "@/lib/staleness";
import type { JobResponse } from "@/lib/types";
import { TailorButton } from "@/components/tailor/TailorButton";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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

/** Format an ISO date string as "18 Jun 2026". */
function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/** Days from now until the given ISO date (negative = in the past). */
function daysUntil(iso: string): number {
  const ms = new Date(iso).setHours(0, 0, 0, 0) - new Date().setHours(0, 0, 0, 0);
  return Math.ceil(ms / 86_400_000);
}

function formatSalaryRange(min?: number | null, max?: number | null): string | null {
  if (!min && !max) return null;
  const fmt = (n: number) =>
    n >= 1000 ? `£${Math.round(n / 1000)}k` : `£${n}`;
  if (min && max) return min === max ? fmt(min) : `${fmt(min)}–${fmt(max)}`;
  if (min) return `${fmt(min)}+`;
  if (max) return `up to ${fmt(max)}`;
  return null;
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

  // ── Primary score ────────────────────────────────────────────────────────────
  // The big badge must show the SAME number the list is sorted by. The dashboard
  // orders rows by COALESCE(llm_fit_score, match_score) — the LLM judge (E4) wins
  // when it has scored the job, else the keyword engine (E1). Showing match_score
  // while sorting by the judge made the badges look scrambled (they were never the
  // sort key). isJudged mirrors the AI-verdict-badge guard further down.
  const isJudged = job.llm_fit_score != null && job.llm_verdict != null;
  const primaryScore = isJudged ? (job.llm_fit_score as number) : job.match_score;

  // ── Date freshness ──────────────────────────────────────────────────────────
  // crawlerTime: the timestamp our crawler last/first saw the listing.
  const crawlerTime = job.last_seen_at ?? job.first_seen_at ?? job.date_found;
  // isLowConfidence: approximate / suspect date — show "~" prefix + muted text.
  const isLowConfidence =
    job.date_confidence === "low" || job.date_confidence === "fabricated";
  // postedLabel: prefix for the posted line.
  const postedPrefix = isLowConfidence ? "~Posted " : "Posted ";

  // ── Deadline ────────────────────────────────────────────────────────────────
  const deadlineDays = job.deadline ? daysUntil(job.deadline) : null;
  const deadlineColor =
    deadlineDays === null
      ? "text-muted-foreground/60"
      : deadlineDays < 0
      ? "text-red-400"
      : deadlineDays <= 7
      ? "text-amber-400"
      : "";
  const deadlineTooltip =
    job.deadline_source === "listing"
      ? "Deadline from the job listing."
      : job.deadline_source === "description"
      ? "Deadline parsed from the listing text — verify on the source site."
      : null;

  const staleness = stalenessBadge(job.staleness_state);

  async function handleApply(e: React.MouseEvent) {
    e.stopPropagation();
    // Open apply URL and track in pipeline
    window.open(safeUrl(job.apply_url), "_blank", "noopener,noreferrer");
    try {
      await createPipelineApplication(job.id);
      posthog.capture("application_created", { job_id: job.id });
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
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          // Don't activate when focus is on an inner button/link
          const target = e.target as HTMLElement;
          if (target.closest("button") || target.closest("a")) return;
          e.preventDefault();
          router.push(`/jobs/${job.id}`);
        }
      }}
      className="glass-card rounded-xl p-4 cursor-pointer flex flex-col gap-3"
      role="link"
      tabIndex={0}
      aria-label={`Job: ${job.title} at ${job.company}`}
    >
      {/* ---- Top row: Score + Title ---- */}
      <div className="flex items-start gap-3">
        {/* Score badge — shows the SAME score the list is sorted by (judge fit
            when judged, else keyword match), colour-matched to that number, with
            a label so its meaning is explicit and it can't be mistaken for the
            other engine's score. */}
        <div className="flex flex-col items-center gap-1 flex-shrink-0">
          <div
            className={`score-badge ${scoreClass(primaryScore, isJudged)} flex items-center justify-center rounded-lg w-11 h-11 text-lg font-bold`}
            aria-label={`${isJudged ? "AI fit" : "Keyword match"} score: ${primaryScore}`}
            title={
              isJudged
                ? "AI judge fit — this is the score the list is ranked by"
                : "Keyword match — not yet AI-ranked"
            }
          >
            {primaryScore}
          </div>
          <span
            className={`text-[10px] font-semibold uppercase tracking-wide leading-none ${
              isJudged ? "text-primary" : "text-muted-foreground"
            }`}
          >
            {isJudged ? "AI fit" : "match"}
          </span>
        </div>

        <div className="min-w-0 flex-1">
          <h3
            className="font-heading font-semibold text-foreground leading-tight line-clamp-2"
            title={job.title}
          >
            {job.title}
          </h3>
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 mt-1 text-sm text-muted-foreground">
            <span className="flex items-center gap-1">
              <Briefcase className="h-3 w-3 flex-shrink-0" aria-hidden="true" />
              <span className="truncate max-w-[120px]" title={job.company}>{job.company}</span>
            </span>
            <span className="hidden sm:inline text-border" aria-hidden="true">|</span>
            <span className="flex items-center gap-1">
              <MapPin className="h-3 w-3 flex-shrink-0" aria-hidden="true" />
              <span className="truncate max-w-[120px]" title={job.location || "Remote"}>{job.location || "Remote"}</span>
            </span>
            <span className="hidden sm:inline text-border" aria-hidden="true">|</span>
            {/* ── Posted / Seen line ── */}
            {job.posted_at ? (
              <Tooltip>
                <TooltipTrigger
                  render={
                    <span
                      className={`flex items-center gap-1${isLowConfidence ? " text-muted-foreground/60" : ""}`}
                      aria-label={`${postedPrefix}${timeAgo(job.posted_at)}`}
                    />
                  }
                >
                  <Clock className="h-3 w-3 flex-shrink-0" aria-hidden="true" />
                  {postedPrefix}{timeAgo(job.posted_at)}
                </TooltipTrigger>
                <TooltipContent>
                  Posted {formatDate(job.posted_at)} · confidence: {job.date_confidence ?? "unknown"}
                </TooltipContent>
              </Tooltip>
            ) : (
              <Tooltip>
                <TooltipTrigger
                  render={
                    <span
                      className="flex items-center gap-1 text-muted-foreground/60"
                      aria-label={`Seen ${timeAgo(crawlerTime)}`}
                    />
                  }
                >
                  <Clock className="h-3 w-3 flex-shrink-0" aria-hidden="true" />
                  Seen {timeAgo(crawlerTime)}
                </TooltipTrigger>
                <TooltipContent>
                  We first saw this listing then; the source didn&apos;t provide a posting date.
                </TooltipContent>
              </Tooltip>
            )}
          </div>

          {/* ── Deadline row ── */}
          <div className="flex items-center gap-x-2 gap-y-0.5 mt-0.5 text-sm">
            {job.deadline ? (
              <Tooltip>
                <TooltipTrigger
                  render={
                    <span
                      className={`flex items-center gap-1 ${deadlineColor}`}
                      aria-label={`Apply by ${formatDate(job.deadline)}`}
                    />
                  }
                >
                  <CalendarClock className="h-3 w-3 flex-shrink-0" aria-hidden="true" />
                  {deadlineDays !== null && deadlineDays < 0
                    ? `Closed ${formatDate(job.deadline)}`
                    : `Apply by ${formatDate(job.deadline)}`}
                </TooltipTrigger>
                {deadlineTooltip && (
                  <TooltipContent>{deadlineTooltip}</TooltipContent>
                )}
              </Tooltip>
            ) : (
              <span
                className="flex items-center gap-1 text-muted-foreground/60"
                aria-label="No deadline listed"
              >
                <CalendarClock className="h-3 w-3 flex-shrink-0" aria-hidden="true" />
                No deadline listed
              </span>
            )}
          </div>
        </div>

        {/* Staleness badge */}
        {staleness && (
          <Tooltip>
            <TooltipTrigger
              render={
                <Badge
                  variant="outline"
                  className={`flex-shrink-0 text-xs gap-1 ${staleness.colorClass}`}
                />
              }
            >
              <AlertTriangle className="h-3 w-3" aria-hidden="true" />
              {staleness.label}
            </TooltipTrigger>
            <TooltipContent>{staleness.description}</TooltipContent>
          </Tooltip>
        )}
      </div>

      {/* ---- Salary + type + seniority + workplace + visa ---- */}
      <div className="flex flex-wrap items-center gap-2">
        {/* Keyword (recall) score — kept visible but clearly secondary, shown only
            when the judge produced the primary score, so the keyword number the
            badge used to display isn't lost. */}
        {isJudged && (
          <Badge
            variant="outline"
            className="text-xs text-muted-foreground/70"
            title="Keyword match score — a recall signal, not the ranking score"
          >
            kw {job.match_score}
          </Badge>
        )}
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

      {/* ---- AI verdict badge ---- */}
      {job.llm_verdict != null && job.llm_fit_score != null && (
        <div className="flex flex-wrap gap-1.5">
          <Badge
            variant="outline"
            title={job.llm_reason ?? undefined}
            className={
              job.llm_fit_score >= 70
                ? "text-xs border-emerald-500 text-emerald-600"
                : job.llm_fit_score >= 40
                ? "text-xs border-amber-500 text-amber-600"
                : "text-xs border-red-500 text-red-600"
            }
          >
            AI: {job.llm_verdict}
          </Badge>
        </div>
      )}

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

        {/* Compact "Tailor my CV" — dashboard card doesn't need to open the job first */}
        <TailorButton jobId={job.id} variant="ghost" size="sm" />

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
        <span
          className="text-xs text-muted-foreground/60 font-mono flex-shrink-0"
          title={job.source}
        >
          {job.source}
        </span>
      </div>
    </div>
  );
}
