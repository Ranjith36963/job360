"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { listApplications } from "@/lib/api";
import type { ApplicationSummary } from "@/lib/api";
import { STATUS_LABEL } from "@/lib/event-labels";
import { relativeTime } from "@/lib/utils";
import { formatDayMonth, formatDateTime } from "@/lib/format-date";
import { VisaBadge } from "@/components/applications/VisaBadge";
import { PRIORITY_LABEL, PRIORITY_OPTIONS } from "@/lib/memory";
import { filterByBrowser, originLabel, preflightProblems, queueSummaryLine } from "@/lib/batch-queue";
import type { QueueBrowserFilter, QueueSummary } from "@/lib/batch-queue";
import { QueueBrowserChip, QueueBrowserFilterRow } from "@/components/needs-you/QueueBrowser";


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

/** Statuses where the ball is moving — these get the one neon status dot. */
const LIVE_STATUSES = new Set([
  "applied",
  "replied",
  "interview_requested",
  "interview_scheduled",
  "interview_done",
  "offer",
  "hired",
]);

/**
 * The calm ledger rows (redesign slice 2) — shared by `/applications` (with its
 * filters) and the signed-in Home (all of them). Hairline separators, not
 * cards. Owner decision, 2026-09-25 — ONE wide row per application: the most
 * important facts (title, company, status) read on the left, the rest (Next,
 * fit, documents/sent, location/visa, last update) fill the right on wide
 * screens; everything stacks on phones.
 */
export function ApplicationRows({ applications }: { applications: ApplicationSummary[] }) {
  return (
    <ul className="flex flex-col">
      {applications.map((app) => {
        const hasCv = (app.artifacts?.cv ?? 0) > 0;
        const hasCoverLetter = (app.artifacts?.cover_letter ?? 0) > 0;
        const showVisa = app.visa_signal === "sponsors" || app.visa_signal === "no_sponsorship";
        const hasFit = typeof app.fit_score === "number";
        // "unknown" shows nothing.
        const lineSide = app.line_side;
        const fitScore = hasFit ? Math.max(0, Math.min(100, app.fit_score as number)) : 0;

        return (
          <li key={app.id} className="border-t border-border first:border-t-0">
            <Link
              href={`/applications/${app.id}`}
              className="flex flex-col gap-3 py-4 transition-colors hover:bg-muted/40 md:flex-row md:items-start md:gap-6"
            >
              {/* 1. Title, company, status — most prominent, reads left on wide screens.
                  Long titles/company names wrap to 2 lines instead of being cut to one
                  with an ellipsis (owner report, 2026-09-25); the `title` attribute
                  carries the full text for anyone who still wants the one-line version
                  on hover. */}
              <div className="min-w-0 md:w-72 md:shrink-0 lg:w-80">
                <p className="line-clamp-2 font-medium" title={titleFor(app)}>
                  {titleFor(app)}
                </p>
                <p className="line-clamp-2 text-sm text-muted-foreground" title={app.job_company}>
                  {app.job_company}
                </p>
                <span className="mt-1.5 inline-flex items-center gap-1.5 font-mono text-[11px] font-medium tracking-wide text-muted-foreground">
                  <i
                    aria-hidden="true"
                    className={`h-1.5 w-1.5 rounded-full ${LIVE_STATUSES.has(app.status) ? "bg-primary" : "bg-faint"}`}
                  />
                  {STATUS_LABEL[app.status] ?? app.status}
                </span>
                {/* Job priority (owner decision 2026-10-19): only when the user set one, and "off" says nothing. */}
                {app.priority && app.priority !== "off" && (
                  <span
                    data-testid="row-priority"
                    className="mt-1.5 inline-block rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground"
                  >
                    {PRIORITY_LABEL[app.priority] ?? app.priority} priority
                  </span>
                )}
                {/* Weak spot #3 wave 2: the headline stage while another assistant session
                    holds this application (mirrors Tsenta's two-line "Tailoring résumé /
                    Reading job description…" status cell; the derivation is Job360's own). */}
                {app.stage && (
                  <p data-testid="row-stage" className="mt-1 text-xs text-warning">
                    {app.stage}
                  </p>
                )}
                {originLabel(app.origin) && (
                  <p data-testid="row-origin" className="mt-1 font-mono text-[10.5px] text-muted-foreground">
                    {originLabel(app.origin)}
                  </p>
                )}
                {/* Batch queue: the place in line and anything that would stop it. */}
                {typeof app.queue_position === "number" && (
                  <p data-testid="row-queue-position" className="mt-1 font-mono text-xs font-medium text-brand">
                    #{app.queue_position} in batch
                  </p>
                )}
                {typeof app.queue_position === "number" && app.browser && (
                  <p className="mt-1">
                    <QueueBrowserChip id={app.id} browser={app.browser} />
                  </p>
                )}
                {preflightProblems(app.preflight).length > 0 && (
                  <ul data-testid="row-preflight" className="mt-1 flex flex-col gap-0.5 text-xs text-warning">
                    {preflightProblems(app.preflight).map((p) => (
                      <li key={p}>{p}</li>
                    ))}
                  </ul>
                )}
              </div>

              {/* 2-6. Everything else — fills the right on wide screens. */}
              <div className="flex min-w-0 flex-1 flex-col gap-1.5">
                {/* 2. Next line, with the interview date/time when there is one. */}
                {(app.next_step?.label || app.interview_at || (app.follow_up_due && app.follow_up_on)) && (
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
                    {app.next_step?.label && (
                      <span data-testid="row-next-step" className="truncate text-brand">
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
                        className="rounded-full bg-warning/15 px-3 py-1 font-medium text-warning"
                      >
                        Follow up {formatDayMonth(app.follow_up_on)}
                      </span>
                    )}
                  </div>
                )}

                {/* 3. Fit — the assistant's verdict is the main thing, the score a small
                    mono note beside it. Silent (rule #29) when no fit has been judged yet. */}
                {hasFit && (
                  <div data-testid="row-fit" className="flex items-baseline gap-2 text-sm">
                    {app.fit_verdict && <span className="min-w-0 truncate">{app.fit_verdict}</span>}
                    <span className="shrink-0 font-mono text-xs text-muted-foreground">{fitScore}/100</span>
                    {lineSide === "above" && (
                      <span data-testid="row-line-side" className="shrink-0 text-xs text-muted-foreground">
                        Above your line
                      </span>
                    )}
                    {lineSide === "below" && (
                      <span data-testid="row-line-side" className="shrink-0 text-xs text-muted-foreground">
                        Below your line
                      </span>
                    )}
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
                <p className="font-mono text-xs text-faint">{relativeTime(app.last_event_at)}</p>
              </div>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

export function ApplicationList({ limit = 50 }: { limit?: number }) {
  const [applications, setApplications] = useState<ApplicationSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("all");
  // Owner decision, 2026-09-25 — client-side, over the already-fetched page,
  // the same way the status chips filter: no separate `due=true` round trip.
  const [dueOnly, setDueOnly] = useState(false);
  // Weak spot #3 wave 2: "In flight" asks the server (`in_flight=true`), because the
  // hold lives in the event log, not in anything the page already fetched.
  const [inFlight, setInFlight] = useState(false);
  // Job priority filter — asks the server (`priority=`): "all" sends nothing.
  const [priority, setPriority] = useState<string>("all");
  // Batch queue: server-side too (`queued=true` gives queue order, preflight and the summary).
  const [queuedOnly, setQueuedOnly] = useState(false);
  const [queueSummary, setQueueSummary] = useState<QueueSummary | null>(null);
  const [browserFilter, setBrowserFilter] = useState<QueueBrowserFilter>("all");
  // A server-filter switch shows "Loading…" in place of the rows (never the old list under the new
  // filter), while the filter bar itself stays on screen.
  const [switching, setSwitching] = useState(false);

  useEffect(() => {
    let cancelled = false;
    listApplications({
      limit,
      ...(queuedOnly ? { queued: true } : inFlight ? { in_flight: true } : {}),
      ...(priority !== "all" ? { priority } : {}),
    })
      .then((res) => {
        if (cancelled) return;
        setApplications(res.applications);
        setSwitching(false);
        setQueueSummary(queuedOnly ? ((res.queue_summary as QueueSummary | null | undefined) ?? null) : null);
        setError(null);
      })
      .catch(() => {
        if (cancelled) return;
        setSwitching(false);
        setError("Could not load your applications.");
      });
    return () => {
      cancelled = true;
    };
  }, [limit, inFlight, queuedOnly, priority]);

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
    if (queuedOnly) out = filterByBrowser(out, browserFilter);
    return out;
  }, [applications, statusFilter, dueOnly, queuedOnly, browserFilter]);

  if (error) {
    return <p className="text-sm text-destructive">{error}</p>;
  }

  if (applications === null) {
    return <p className="text-sm text-muted-foreground">Loading your applications…</p>;
  }

  if (applications.length === 0 && !inFlight && !queuedOnly && priority === "all") {
    return (
      <p className="text-sm text-muted-foreground">
        Nothing yet. Bring a job from your assistant, or the{" "}
        <Link href="/bring" className="text-brand underline">
          Jobs in
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
          onClick={() => {
            setStatusFilter("all");
            if (inFlight || queuedOnly) setSwitching(true);
            setInFlight(false);
            setQueuedOnly(false);
          }}
          aria-pressed={statusFilter === "all" && !inFlight && !queuedOnly}
          className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
            statusFilter === "all" && !inFlight && !queuedOnly
              ? "bg-primary text-primary-foreground"
              : "bg-primary/10 text-brand hover:bg-primary/20"
          }`}
        >
          All{inFlight || queuedOnly ? "" : ` (${applications.length})`}
        </button>
        <button
          type="button"
          data-testid="queued-filter"
          onClick={() => {
            setSwitching(true);
            setQueuedOnly((v) => !v);
            setInFlight(false);
            setStatusFilter("all");
          }}
          aria-pressed={queuedOnly}
          className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
            queuedOnly ? "bg-primary text-primary-foreground" : "bg-primary/10 text-brand hover:bg-primary/20"
          }`}
        >
          Queued
        </button>
        <button
          type="button"
          data-testid="in-flight-filter"
          onClick={() => {
            setSwitching(true);
            setInFlight((v) => !v);
            setQueuedOnly(false);
          }}
          aria-pressed={inFlight}
          className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
            inFlight
              ? "bg-warning text-background"
              : "bg-warning/10 text-warning hover:bg-warning/20"
          }`}
        >
          In flight
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
                : "bg-primary/10 text-brand hover:bg-primary/20"
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
                ? "bg-warning text-background"
                : "bg-warning/10 text-warning hover:bg-warning/20"
            }`}
          >
            Due ({dueCount})
          </button>
        )}
        <label className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
          Priority:
          <select
            data-testid="priority-filter"
            value={priority}
            onChange={(e) => {
              setSwitching(true);
              setPriority(e.target.value);
            }}
            className="h-8 rounded-lg border border-border bg-background px-2 text-xs text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <option value="all">All</option>
            {PRIORITY_OPTIONS.map((o) => (
              <option key={o} value={o}>{PRIORITY_LABEL[o]}</option>
            ))}
            <option value="not_set">Not set</option>
          </select>
        </label>
      </div>

      {queuedOnly && queueSummaryLine(queueSummary) && (
        <p data-testid="queue-summary" className="font-mono text-xs text-muted-foreground">
          {queueSummaryLine(queueSummary)}
        </p>
      )}

      {queuedOnly && !switching && (
        <QueueBrowserFilterRow
          summary={queueSummary}
          total={applications.length}
          value={browserFilter}
          onChange={setBrowserFilter}
        />
      )}

      {switching ? (
        <p className="text-sm text-muted-foreground">Loading your applications…</p>
      ) : visibleApplications && visibleApplications.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {queuedOnly
            ? "Your batch is empty. Bring a job and leave Add to batch ticked, or let your AI hunt."
            : inFlight
              ? "Nothing is in flight right now."
              : priority !== "all"
                ? "No applications with this priority."
                : "No applications with this status."}
        </p>
      ) : (
        <ApplicationRows applications={visibleApplications ?? []} />
      )}
    </div>
  );
}
