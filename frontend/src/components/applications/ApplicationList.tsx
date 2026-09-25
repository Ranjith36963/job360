"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { listApplications } from "@/lib/api";
import type { ApplicationSummary } from "@/lib/api";
import { STATUS_LABEL } from "@/lib/event-labels";
import { relativeTime } from "@/lib/utils";
import { formatDayMonth, formatDateTime } from "@/lib/format-date";
import { VisaBadge } from "@/components/applications/VisaBadge";


// The status vocabulary is closed in the backend (src/core/settings.py
// APPLICATION_STATUS_EVENT_TYPES) — STATUS_LABEL (src/lib/event-labels.ts)
// is display copy only, never a second source of truth for which statuses
// exist.

/**
 * The applications home + the `/applications` list page share this list —
 * both render the same summary rows (spec R11 `GET /applications`).
 *
 * "Mark Applied" writes a permanent receipt (spec R8) — a real-world fact
 * ("I applied"), not a status toggle — so it lives only on the application
 * page now, behind a confirm, never here in a scannable list a stray click
 * could hit.
 */

/** "Untitled role" → the company → the ad link's host → "Untitled job"
 * (owner decision, 2026-09-24). Never blank: something must sit in the
 * title slot for a card to be scannable. */
function titleFor(app: ApplicationSummary): string {
  if (app.job_title) return app.job_title;
  if (app.job_company) return app.job_company;
  if (app.job_url) {
    try {
      return new URL(app.job_url).host;
    } catch {
      // Not a parseable absolute URL — fall through to the last resort.
    }
  }
  return "Untitled job";
}

export function ApplicationList({ limit = 50 }: { limit?: number }) {
  const [applications, setApplications] = useState<ApplicationSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("all");
  // Owner decision, 2026-09-25 — client-side, over the already-fetched page,
  // the same way the status chips filter: no separate `due=true` round trip.
  const [dueOnly, setDueOnly] = useState(false);

  useEffect(() => {
    let cancelled = false;
    listApplications({ limit })
      .then((res) => {
        if (cancelled) return;
        setApplications(res.applications);
        setError(null);
      })
      .catch(() => {
        if (cancelled) return;
        setError("Could not load your applications.");
      });
    return () => {
      cancelled = true;
    };
  }, [limit]);

  // Browser-side only — one chip per status actually present in this list,
  // plus "All". Counts move with the data; there is no server-side facet
  // query for this.
  const statusCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const app of applications ?? []) {
      counts.set(app.status, (counts.get(app.status) ?? 0) + 1);
    }
    return counts;
  }, [applications]);

  const dueCount = useMemo(
    () => (applications ?? []).filter((app) => app.follow_up_due).length,
    [applications]
  );

  const visibleApplications = useMemo(() => {
    if (!applications) return applications;
    let out = applications;
    if (statusFilter !== "all") out = out.filter((app) => app.status === statusFilter);
    if (dueOnly) out = out.filter((app) => app.follow_up_due);
    return out;
  }, [applications, statusFilter, dueOnly]);

  if (error) {
    return <p className="text-sm text-destructive">{error}</p>;
  }

  if (applications === null) {
    return <p className="text-sm text-muted-foreground">Loading your applications…</p>;
  }

  if (applications.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        Nothing yet. Bring a job from your agent, or the{" "}
        <Link href="/bring" className="text-primary underline">
          Bring a job
        </Link>{" "}
        page, to start your record.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div data-testid="status-filter" className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => setStatusFilter("all")}
          aria-pressed={statusFilter === "all"}
          className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
            statusFilter === "all"
              ? "bg-primary text-primary-foreground"
              : "bg-primary/10 text-primary hover:bg-primary/20"
          }`}
        >
          All ({applications.length})
        </button>
        {[...statusCounts.entries()].map(([status, count]) => (
          <button
            key={status}
            type="button"
            onClick={() => setStatusFilter(status)}
            aria-pressed={statusFilter === status}
            className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
              statusFilter === status
                ? "bg-primary text-primary-foreground"
                : "bg-primary/10 text-primary hover:bg-primary/20"
            }`}
          >
            {STATUS_LABEL[status] ?? status} ({count})
          </button>
        ))}
        {dueCount > 0 && (
          <button
            type="button"
            data-testid="due-filter"
            onClick={() => setDueOnly((v) => !v)}
            aria-pressed={dueOnly}
            className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
              dueOnly
                ? "bg-amber-500 text-amber-950"
                : "bg-amber-500/10 text-amber-600 hover:bg-amber-500/20 dark:text-amber-400"
            }`}
          >
            Due ({dueCount})
          </button>
        )}
      </div>

      {visibleApplications && visibleApplications.length === 0 ? (
        <p className="text-sm text-muted-foreground">No applications with this status.</p>
      ) : (
        // Owner decision, 2026-09-25 — ONE wide row per application instead
        // of a two-up grid, laid out so the most important facts (title,
        // company, status) read on the left and the rest (Next, fit,
        // documents/sent, location/visa, last update) fill the right on wide
        // screens; everything stacks on phones.
        <ul className="flex flex-col gap-3">
          {visibleApplications?.map((app) => {
            const hasCv = (app.artifacts?.cv ?? 0) > 0;
            const hasCoverLetter = (app.artifacts?.cover_letter ?? 0) > 0;
            const showVisa = app.visa_signal === "sponsors" || app.visa_signal === "no_sponsorship";
            const hasFit = typeof app.fit_score === "number";
            const fitScore = hasFit ? Math.max(0, Math.min(100, app.fit_score as number)) : 0;

            return (
              <li key={app.id} className="glass-card rounded-xl p-4">
                <Link
                  href={`/applications/${app.id}`}
                  className="flex flex-col gap-3 md:flex-row md:items-start md:gap-6"
                >
                  {/* 1. Title, company, status — most prominent, reads left on wide screens. */}
                  <div className="min-w-0 md:w-64 md:shrink-0">
                    <p className="truncate font-semibold">{titleFor(app)}</p>
                    <p className="truncate text-sm text-muted-foreground">{app.job_company}</p>
                    <span className="mt-1.5 inline-block rounded-full bg-primary/10 px-3 py-1 text-xs font-medium text-primary">
                      {STATUS_LABEL[app.status] ?? app.status}
                    </span>
                  </div>

                  {/* 2-6. Everything else — fills the right on wide screens. */}
                  <div className="flex min-w-0 flex-1 flex-col gap-1.5">
                    {/* 2. Next line, with the interview date/time when there is one. */}
                    {(app.next_step?.label || app.interview_at || (app.follow_up_due && app.follow_up_on)) && (
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
                        {app.next_step?.label && (
                          <span data-testid="row-next-step" className="truncate text-primary">
                            Next: {app.next_step.label}
                          </span>
                        )}
                        {app.interview_at && (
                          <span
                            data-testid="row-interview"
                            className="rounded-full bg-accent/20 px-2 py-0.5 font-medium text-accent-foreground"
                          >
                            Interview {formatDateTime(app.interview_at)}
                          </span>
                        )}
                        {app.follow_up_due && app.follow_up_on && (
                          <span
                            data-testid="row-follow-up"
                            className="rounded-full bg-amber-500/15 px-3 py-1 font-medium text-amber-600 dark:text-amber-400"
                          >
                            Follow up {formatDayMonth(app.follow_up_on)}
                          </span>
                        )}
                      </div>
                    )}

                    {/* 3. Fit — silent (rule #29) when no fit has been judged yet. */}
                    {hasFit && (
                      <div data-testid="row-fit" className="flex items-center gap-2 text-xs text-muted-foreground">
                        <span className="h-1.5 w-16 shrink-0 overflow-hidden rounded-full bg-muted">
                          <span className="block h-full rounded-full bg-primary" style={{ width: `${fitScore}%` }} />
                        </span>
                        <span className="shrink-0 font-medium text-foreground">{fitScore}/100</span>
                        {app.fit_verdict && <span className="truncate">{app.fit_verdict}</span>}
                      </div>
                    )}

                    {/* 4. Documents + sent — only the tags that actually exist. */}
                    {(hasCv || hasCoverLetter || app.last_receipt_at) && (
                      <div className="flex flex-wrap gap-1.5 text-xs text-muted-foreground">
                        {hasCv && <span className="rounded-full bg-muted px-2 py-0.5">CV ✓</span>}
                        {hasCoverLetter && (
                          <span className="rounded-full bg-muted px-2 py-0.5">Cover letter ✓</span>
                        )}
                        {app.last_receipt_at && (
                          <span data-testid="row-sent" className="rounded-full bg-muted px-2 py-0.5">
                            Sent {formatDayMonth(app.last_receipt_at)}
                          </span>
                        )}
                      </div>
                    )}

                    {/* 5. Location + visa — omit empty parts; visa only when recorded. */}
                    {(app.job_location || showVisa) && (
                      <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                        {app.job_location && <span className="truncate">{app.job_location}</span>}
                        <VisaBadge
                          signal={app.visa_signal ?? "unknown"}
                          needsSponsorship={app.needs_sponsorship}
                        />
                      </div>
                    )}

                    {/* 6. Last update — relative, least prominent. */}
                    <p className="text-xs text-muted-foreground/70">{relativeTime(app.last_event_at)}</p>
                  </div>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
