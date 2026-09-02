"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import posthog from "posthog-js";
import { toast } from "@/lib/toast";
import {
  ArrowLeft,
  ExternalLink,
  Heart,
  X,
  Check,
  ArrowRightLeft,
  MapPin,
  Building2,
  Calendar,
  CalendarClock,
  Briefcase,
  Shield,
  Star,
  AlertTriangle,
} from "lucide-react";
import { DedupGroupViewer } from "@/components/jobs/DedupGroupViewer";

import { getJob, setJobAction, removeJobAction } from "@/lib/api";
import { safeUrl } from "@/lib/utils";
import { scoreClass } from "@/lib/scoring";
import { stalenessBadge } from "@/lib/staleness";
import type { JobResponse } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { ScoreRadar } from "@/components/jobs/ScoreRadar";
import { ScoreCounter } from "@/components/jobs/ScoreCounter";
import { ApplyButton } from "@/components/jobs/ApplyButton";
import { ReceiptButton } from "@/components/jobs/ReceiptButton";
import { TailorSection } from "@/components/tailor/TailorSection";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function relativeDate(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const hours = Math.floor(diff / 3_600_000);
  if (hours < 1) return "Just now";
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days === 1) return "Yesterday";
  if (days < 7) return `${days}d ago`;
  const weeks = Math.floor(days / 7);
  if (weeks < 5) return `${weeks}w ago`;
  return new Date(iso).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

function daysUntil(iso: string): number {
  return Math.ceil(
    (new Date(iso).setHours(0, 0, 0, 0) - new Date().setHours(0, 0, 0, 0)) /
      86_400_000
  );
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function JobDetailSkeleton() {
  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <Skeleton className="mb-6 h-5 w-40" />
      <div className="flex flex-col gap-8 lg:flex-row">
        <div className="flex-1 space-y-4 lg:w-3/5">
          <Skeleton className="h-8 w-3/4" />
          <Skeleton className="h-5 w-1/2" />
          <Skeleton className="h-4 w-1/3" />
          <div className="flex gap-2">
            <Skeleton className="h-5 w-20" />
            <Skeleton className="h-5 w-20" />
          </div>
          <Skeleton className="mt-6 h-px w-full" />
          <Skeleton className="h-16 w-32" />
          <Skeleton className="mt-6 h-px w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
        <div className="space-y-6 lg:w-2/5">
          <Skeleton className="mx-auto h-[300px] w-[300px] rounded-2xl" />
          <Skeleton className="h-32 w-full rounded-xl" />
          <Skeleton className="h-10 w-full rounded-lg" />
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Client component
// ---------------------------------------------------------------------------

export function JobDetailClient({ jobId }: { jobId: number }) {
  const params = useParams<{ id: string }>();
  const resolvedId = jobId || Number(params.id);

  const [job, setJob] = useState<JobResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);

  // Fetch job
  useEffect(() => {
    if (!resolvedId || Number.isNaN(resolvedId)) {
      setError("Invalid job ID");
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    getJob(resolvedId)
      .then((data) => {
        if (!cancelled) setJob(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const msg = err instanceof Error ? err.message : "Failed to load job";
          // Try to extract the detail from API error JSON
          const detailMatch = msg.match(/"detail":"([^"]+)"/);
          setError(detailMatch ? detailMatch[1] : "This job may have been removed or doesn't exist.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [resolvedId]);

  // Sync browser tab title after client-side (soft) navigation.
  // generateMetadata is SSR-only — it does not re-run on in-app nav, so the
  // tab title is stale until this effect fires.  Guard on `job` so the
  // loading / error states never clobber the SSR title.
  useEffect(() => {
    if (job) {
      document.title = `${job.title} at ${job.company} — Job360`;
    }
  }, [job]);

  // Fire once per view when the job finishes loading — keyed on job.id so it
  // doesn't re-fire on unrelated re-renders (e.g. like/dismiss action state).
  useEffect(() => {
    if (job) {
      posthog.capture("job_viewed", { job_id: job.id });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.id]);

  // Toggle action
  const handleAction = useCallback(
    async (action: "liked" | "not_interested") => {
      if (!job || actionLoading) return;
      setActionLoading(true);
      try {
        if (job.action === action) {
          await removeJobAction(job.id);
          setJob((prev) => (prev ? { ...prev, action: null } : prev));
        } else {
          await setJobAction(job.id, { action, notes: "" });
          setJob((prev) => (prev ? { ...prev, action } : prev));
        }
      } catch (err) {
        // Was a silent catch: the button simply did not change state, with no
        // toast and no error text, so a failed save was indistinguishable from
        // a click that never registered. Users re-click or assume it is broken.
        toast.apiError(err, "Couldn't update this job — please try again.");
      } finally {
        setActionLoading(false);
      }
    },
    [job, actionLoading]
  );

  // ---- States ----

  if (loading) return <JobDetailSkeleton />;

  if (error || !job) {
    return (
      <div className="mx-auto flex max-w-6xl flex-col items-center justify-center gap-4 px-4 py-24 text-center">
        <AlertTriangle className="h-10 w-10 text-destructive" />
        <h2 className="font-heading text-xl font-semibold">Job not found</h2>
        <p className="text-sm text-muted-foreground">
          {error || "The job you are looking for does not exist or has been removed."}
        </p>
        <Link href="/dashboard">
          <Button variant="outline" size="sm" className="gap-2">
            <ArrowLeft className="h-4 w-4" />
            Back to Dashboard
          </Button>
        </Link>
      </div>
    );
  }

  // Primary score = the AI judge fit when the job has been judged, else the
  // keyword match — the same rule the dashboard sorts and displays by, so the big
  // number doesn't change meaning when you click from a card into its detail page.
  const isJudged = job.llm_fit_score != null && job.llm_verdict != null;
  const primaryScore = isJudged ? (job.llm_fit_score as number) : job.match_score;
  const bucket = scoreClass(primaryScore, isJudged);

  // Deadline derived values
  const deadlineDays = job.deadline ? daysUntil(job.deadline) : null;
  const deadlineColor =
    deadlineDays === null
      ? ""
      : deadlineDays < 0
      ? "text-red-400"
      : deadlineDays <= 7
      ? "text-amber-400"
      : "";
  const deadlineNote =
    job.deadline_source === "listing"
      ? "from listing"
      : job.deadline_source === "description"
      ? "parsed from listing text"
      : null;

  // Low-confidence date: dim the posted-at span
  const isLowConfidenceDate =
    job.date_confidence === "low" || job.date_confidence === "fabricated";

  const staleness = stalenessBadge(job.staleness_state);

  return (
    <div className="relative">
      <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="aurora-glow-top" />
        <div className="aurora-glow-right" />
      </div>

      <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
        {/* Back link */}
        <div className="animate-fade-in-up">
          <Link
            href="/dashboard"
            className="mb-6 inline-flex items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
          >
            <ArrowLeft className="h-4 w-4" />
            Back to Dashboard
          </Link>
        </div>

        <div className="flex flex-col gap-8 lg:flex-row">
          {/* ============================================================
              LEFT COLUMN — Job details
              ============================================================ */}
          <div className="flex flex-col gap-6 lg:w-3/5 animate-fade-in-up stagger-1">
            {/* Title + meta */}
            <div className="space-y-3">
              <h1 className="font-heading text-2xl font-bold leading-tight">
                {job.title}
              </h1>

              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
                <span className="inline-flex items-center gap-1">
                  <Building2 className="h-3.5 w-3.5" />
                  {job.company}
                </span>
                <span className="inline-flex items-center gap-1">
                  <MapPin className="h-3.5 w-3.5" />
                  {job.location}
                </span>
                <span className="inline-flex items-center gap-1">
                  <Calendar className="h-3.5 w-3.5" />
                  {relativeDate(job.date_found)}
                </span>
              </div>

              {/* Structured salary (preferred) or legacy salary string.
                  The API only ever fills salary_min_gbp/max_gbp with a real,
                  annualised GBP figure (F6 — src/api/routes/jobs.py
                  _parse_enr_salary leaves them unset rather than guess when
                  the source currency can't be converted), so the "GBP"
                  check below is defence-in-depth, not the primary guard —
                  and the value is always annual already, so no period
                  suffix belongs here. */}
              {/* NO currency check here, deliberately. salary_currency_original
                  is the ORIGINAL currency and is still echoed on rows the API
                  has correctly converted ("USD" on a job now expressed in
                  annual GBP), so gating on it hid every valid conversion on
                  this page while JobCard — which has no such check — showed
                  the same job's salary. One value, two surfaces, two answers.
                  The real guard is upstream: _parse_enr_salary leaves the
                  amounts UNSET when it cannot convert, so a present value is
                  always honest annual GBP. */}
              {(job.salary_min_gbp || job.salary_max_gbp) ? (
                <p className="text-sm font-medium text-foreground">
                  {job.salary_min_gbp && job.salary_max_gbp
                    ? `£${Math.round(job.salary_min_gbp / 1000)}k – £${Math.round(job.salary_max_gbp / 1000)}k`
                    : job.salary_min_gbp
                    ? `£${Math.round(job.salary_min_gbp / 1000)}k+`
                    : `up to £${Math.round((job.salary_max_gbp ?? 0) / 1000)}k`}
                </p>
              ) : job.salary ? (
                <p className="text-sm font-medium text-foreground">{job.salary}</p>
              ) : null}

              {/* Badges row */}
              <div className="flex flex-wrap gap-2">
                {/* Enrichment-based seniority (preferred) */}
                {job.seniority ? (
                  <Badge variant="secondary" className="gap-1 capitalize">
                    <Star className="h-3 w-3" />
                    {job.seniority.replace(/_/g, " ")}
                  </Badge>
                ) : job.experience_level ? (
                  <Badge variant="secondary" className="gap-1">
                    <Star className="h-3 w-3" />
                    {job.experience_level}
                  </Badge>
                ) : null}

                {/* Workplace type */}
                {job.workplace_type && (
                  <Badge variant="secondary" className="gap-1 capitalize">
                    <MapPin className="h-3 w-3" />
                    {job.workplace_type.replace(/_/g, " ")}
                  </Badge>
                )}

                {/* Employment type */}
                {job.employment_type && (
                  <Badge variant="secondary" className="gap-1 capitalize">
                    <Briefcase className="h-3 w-3" />
                    {job.employment_type.replace(/_/g, " ")}
                  </Badge>
                )}

                {/* Visa sponsorship */}
                {(job.visa_sponsorship === true || job.visa_flag) && (
                  <Badge variant="outline" className="gap-1 border-primary/30 text-primary">
                    <Shield className="h-3 w-3" />
                    Visa Sponsorship
                  </Badge>
                )}

                {/* Industry */}
                {job.industry && (
                  <Badge variant="outline" className="capitalize">
                    {job.industry}
                  </Badge>
                )}

                <Badge variant="outline" className="capitalize">
                  {job.source.replace(/_/g, " ")}
                </Badge>
              </div>

              {/* Date model display */}
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                {job.posted_at && (
                  <span
                    className={`flex items-center gap-1${isLowConfidenceDate ? " opacity-60" : ""}`}
                  >
                    <Calendar className="h-3 w-3" />
                    Posted {relativeDate(job.posted_at)}
                    {isLowConfidenceDate && (
                      <span className="opacity-70">· approx</span>
                    )}
                  </span>
                )}
                {job.last_seen_at && (
                  <span className="flex items-center gap-1">
                    <Calendar className="h-3 w-3" />
                    Last seen {relativeDate(job.last_seen_at)}
                  </span>
                )}
                {job.deadline ? (
                  <span className={`flex items-center gap-1 ${deadlineColor}`}>
                    <CalendarClock className="h-3 w-3" />
                    {deadlineDays !== null && deadlineDays < 0
                      ? `Closed ${formatDate(job.deadline)}`
                      : `Apply by ${formatDate(job.deadline)}`}
                    {deadlineNote && (
                      <span className="opacity-70 ml-0.5">· {deadlineNote}</span>
                    )}
                  </span>
                ) : (
                  <span className="flex items-center gap-1 opacity-60">
                    <CalendarClock className="h-3 w-3" />
                    No deadline listed
                  </span>
                )}
                {staleness && (
                  <Badge variant="outline" className={`text-xs gap-1 ${staleness.colorClass}`}>
                    <AlertTriangle className="h-3 w-3" />
                    {staleness.label}
                  </Badge>
                )}
              </div>
            </div>

            <Separator />

            {/* Primary score — large display. Shows the AI judge fit when judged
                (the number the list is ranked by), else the keyword match. */}
            <div className="space-y-2">
              <h2 className="font-heading text-sm font-semibold uppercase tracking-wider text-primary/80">
                {isJudged ? "AI Fit Score" : "Match Score"}
              </h2>
              <div
                className={`score-badge ${bucket} inline-flex items-center gap-2 rounded-xl px-5 py-3`}
              >
                <span className="font-mono text-3xl font-bold tabular-nums">
                  <ScoreCounter value={primaryScore} />
                </span>
                <span className="text-sm opacity-70">/ 100</span>
              </div>
              {isJudged && (
                <div className="space-y-1 pt-1">
                  {job.llm_verdict && (
                    <p className="text-sm text-foreground/80">
                      <span className="font-medium text-foreground">AI verdict:</span>{" "}
                      {job.llm_verdict}
                      {job.llm_reason ? ` — ${job.llm_reason}` : ""}
                    </p>
                  )}
                  <p className="text-xs text-muted-foreground">
                    Keyword match score: {job.match_score}/100 — a recall signal, not the ranking score
                  </p>
                </div>
              )}
            </div>

            <Separator />

            {/* Enrichment fields */}
            {(job.required_skills?.length || job.years_experience_min || job.title_canonical) && (
              <div className="space-y-3">
                <h2 className="font-heading text-sm font-semibold uppercase tracking-wider text-primary/80">
                  Role Details
                </h2>
                {job.title_canonical && job.title_canonical !== job.title && (
                  <p className="text-xs text-muted-foreground">
                    Canonical title: <span className="text-foreground font-medium">{job.title_canonical}</span>
                  </p>
                )}
                {job.years_experience_min != null && (
                  <p className="text-sm text-muted-foreground">
                    Experience required:{" "}
                    <span className="font-medium text-foreground">{job.years_experience_min}+ years</span>
                  </p>
                )}
                {job.required_skills && job.required_skills.length > 0 && (
                  <div className="space-y-1.5">
                    <p className="text-xs font-medium text-muted-foreground">Required Skills</p>
                    <div className="flex flex-wrap gap-1.5">
                      {job.required_skills.map((s) => (
                        <Badge key={s} variant="secondary" className="text-xs">
                          {s}
                        </Badge>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* About this role */}
            <div className="space-y-3">
              <h2 className="font-heading text-sm font-semibold uppercase tracking-wider text-primary/80">
                About this role
              </h2>
              {job.source === "user_brought" && job.description ? (
                // The user pasted this ad (there may be no source website to
                // read it on), so show it as written.
                <p
                  data-testid="brought-description"
                  className="whitespace-pre-wrap text-sm leading-relaxed text-muted-foreground"
                >
                  {job.description}
                </p>
              ) : (
                <p className="text-sm leading-relaxed text-muted-foreground">
                  Full job descriptions are available on the source website. Click
                  the button below to view the complete listing and apply.
                </p>
              )}
              {job.apply_url && (
                <a href={safeUrl(job.apply_url)} target="_blank" rel="noopener noreferrer">
                  <Button
                    variant="outline"
                    size="sm"
                    className="gap-2 border-primary/20 text-primary hover:bg-primary/10"
                  >
                    <ExternalLink className="h-3.5 w-3.5" />
                    {job.source === "user_brought"
                      ? "Open the ad"
                      : "View full description on source website"}
                  </Button>
                </a>
              )}
            </div>

            {/* Tailor with AI — prominent section (spec §3.5): CV + cover letter,
                below the description, in the wide main column. */}
            <TailorSection jobId={job.id} />

            {/* Cross-source duplicates */}
            <DedupGroupViewer jobId={job.id} />
          </div>

          {/* ============================================================
              RIGHT COLUMN — Radar + Skills + Actions
              ============================================================ */}
          <div className="flex flex-col gap-6 lg:w-2/5">
            {/* Score Radar */}
            <div className="glass-card rounded-2xl p-6 animate-fade-in-up stagger-2">
              <h3 className="mb-4 text-center font-heading text-sm font-semibold uppercase tracking-wider text-primary/80">
                Score Drivers
              </h3>
              {/* "Score Drivers", not "8D Score Breakdown": these axes do NOT
                  break down the score — they can't be summed to reach it (raw
                  max 130 clamped to 100, and the title/location penalties land
                  on no axis). The headline total is rendered from match_score
                  elsewhere on this page. */}
              <ScoreRadar
                scores={{
                  role: job.role,
                  skill: job.skill,
                  location_score: job.location_score,
                  recency: job.recency,
                  seniority_score: job.seniority_score,
                  salary_score: job.salary_score,
                  visa_score: job.visa_score,
                  workplace_score: job.workplace_score,
                }}
                dimsActive={job.dims_active}
                size={300}
              />
            </div>

            {/* Skill Analysis */}
            <div className="glass-card rounded-2xl p-6 space-y-4 animate-fade-in-up stagger-3">
              <h3 className="font-heading text-sm font-semibold uppercase tracking-wider text-primary/80">
                Skill Analysis
              </h3>

              {/* Matched */}
              {job.matched_skills.length > 0 && (
                <div className="space-y-2">
                  <p className="text-xs font-medium text-muted-foreground">
                    Matched Skills
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {job.matched_skills.map((s) => (
                      <span
                        key={s}
                        className="skill-matched inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium"
                      >
                        <Check className="h-3 w-3" />
                        {s}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Missing */}
              {job.missing_required.length > 0 && (
                <div className="space-y-2">
                  <p className="text-xs font-medium text-muted-foreground">
                    Missing Required
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {job.missing_required.map((s) => (
                      <span
                        key={s}
                        className="skill-missing inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium"
                      >
                        <X className="h-3 w-3" />
                        {s}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Transferable */}
              {job.transferable_skills.length > 0 && (
                <div className="space-y-2">
                  <p className="text-xs font-medium text-muted-foreground">
                    Transferable Skills
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {job.transferable_skills.map((s) => (
                      <span
                        key={s}
                        className="skill-transferable inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium"
                      >
                        <ArrowRightLeft className="h-3 w-3" />
                        {s}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {job.matched_skills.length === 0 &&
                job.missing_required.length === 0 &&
                job.transferable_skills.length === 0 && (
                  <p className="text-xs text-muted-foreground">
                    No skill analysis data available for this job.
                  </p>
                )}
            </div>

            {/* Penalty warning */}
            {job.penalty < 0 && (
              <div className="flex items-start gap-3 rounded-xl border border-destructive/20 bg-destructive/5 px-4 py-3">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
                <div>
                  <p className="text-sm font-medium text-destructive">
                    Penalty Applied
                  </p>
                  <p className="text-xs text-muted-foreground">
                    This job received a{" "}
                    <span className="font-mono font-semibold text-destructive">
                      {job.penalty}
                    </span>{" "}
                    point penalty due to negative keyword or company matches.
                  </p>
                </div>
              </div>
            )}

            {/* Action buttons */}
            <div className="glass-card flex flex-col gap-3 rounded-2xl p-6 animate-fade-in-up stagger-4">
              {/* Apply Now — opens URL + tracks in pipeline. Hidden when the
                  user brought the ad without a link: there is nothing to open. */}
              {job.apply_url && <ApplyButton job={job} fullWidth />}

              {/* I applied — freezes the receipt (what was sent, forever). */}
              <ReceiptButton
                job={job}
                onApplied={() =>
                  setJob((prev) => (prev ? { ...prev, action: "applied" } : prev))
                }
              />

              <div className="flex gap-3">
                {/* Like toggle */}
                <Button
                  variant={job.action === "liked" ? "default" : "outline"}
                  className={`flex-1 gap-2 ${
                    job.action === "liked"
                      ? "bg-primary/15 text-primary border-primary/30 hover:bg-primary/25"
                      : "hover:border-primary/30 hover:text-primary"
                  }`}
                  disabled={actionLoading}
                  onClick={() => handleAction("liked")}
                >
                  <Heart
                    className={`h-4 w-4 ${
                      job.action === "liked" ? "fill-primary" : ""
                    }`}
                  />
                  {job.action === "liked" ? "Liked" : "Like"}
                </Button>

                {/* Not Interested */}
                <Button
                  variant={job.action === "not_interested" ? "default" : "outline"}
                  className={`flex-1 gap-2 ${
                    job.action === "not_interested"
                      ? "bg-destructive/15 text-destructive border-destructive/30 hover:bg-destructive/25"
                      : "hover:border-destructive/30 hover:text-destructive"
                  }`}
                  disabled={actionLoading}
                  onClick={() => handleAction("not_interested")}
                >
                  <X className="h-4 w-4" />
                  {job.action === "not_interested" ? "Dismissed" : "Not Interested"}
                </Button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
