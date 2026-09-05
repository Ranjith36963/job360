"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { listApplications, recordApplicationReceipt } from "@/lib/api";
import type { ApplicationSummary } from "@/lib/api";

// The status vocabulary is closed in the backend (src/core/settings.py
// APPLICATION_STATUS_EVENT_TYPES) — this is display copy only, never a
// second source of truth for which statuses exist.
const STATUS_LABEL: Record<string, string> = {
  considering: "Considering",
  applied: "Applied",
  replied: "Replied",
  interview_requested: "Interview requested",
  interview_scheduled: "Interview scheduled",
  interview_done: "Interview done",
  offer: "Offer",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
  ghosted: "Ghosted",
};

/**
 * The applications home + the `/applications` list page share this list —
 * both render the same summary rows (spec R11 `GET /applications`).
 *
 * "Mark Applied" is the fastest path from "considering" to a receipt: it
 * calls `record_application` (spec R8) with no artifact named, so it freezes
 * whatever the newest CV/cover-letter version already is (or none).
 */
export function ApplicationList({ limit = 50 }: { limit?: number }) {
  const [applications, setApplications] = useState<ApplicationSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [markingId, setMarkingId] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await listApplications({ limit });
      setApplications(res.applications);
      setError(null);
    } catch {
      setError("Could not load your applications.");
    }
  }, [limit]);

  useEffect(() => {
    void load();
  }, [load]);

  const markApplied = useCallback(
    async (id: number) => {
      setMarkingId(id);
      try {
        await recordApplicationReceipt(id, {});
        await load();
      } catch (err) {
        // C10 (application-spine review) — an unhandled rejection here left
        // the button stuck on "Marking…" forever with no feedback: the
        // `finally` below always clears `markingId`, but nothing ever told
        // the user the call actually failed.
        const msg = err instanceof Error ? err.message : "Could not mark this application applied.";
        toast.error(msg);
      } finally {
        setMarkingId(null);
      }
    },
    [load]
  );

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
    <ul className="flex flex-col gap-3">
      {applications.map((app) => (
        <li
          key={app.id}
          className="glass-card flex items-center justify-between gap-4 rounded-xl p-4"
        >
          <Link href={`/applications/${app.id}`} className="min-w-0 flex-1">
            <p className="truncate font-semibold">{app.job_title || "Untitled role"}</p>
            <p className="truncate text-sm text-muted-foreground">{app.job_company}</p>
          </Link>
          <div className="flex shrink-0 items-center gap-3">
            <span className="rounded-full bg-primary/10 px-3 py-1 text-xs font-medium text-primary">
              {STATUS_LABEL[app.status] ?? app.status}
            </span>
            {app.status === "considering" && (
              <button
                type="button"
                onClick={() => void markApplied(app.id)}
                disabled={markingId === app.id}
                className="rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
              >
                {markingId === app.id ? "Marking…" : "Mark Applied"}
              </button>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}
