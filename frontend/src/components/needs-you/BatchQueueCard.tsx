"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { toast } from "sonner";
import { listApplications, recordApplicationEvent } from "@/lib/api";
import { apiErrorMessage } from "@/lib/api-error";
import { originLabel, preflightProblems, queueSummaryLine } from "@/lib/batch-queue";
import type { QueueSummary } from "@/lib/batch-queue";

const LABEL = "font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint";

type QueueRow = {
  id: number;
  queue_position?: number | null;
  origin?: string;
  job_title: string;
  job_company?: string | null;
  preflight?: Record<string, unknown> | null;
};

/** The jobs lined up for your assistant to apply to, in order, with anything wrong in plain words. */
export function BatchQueueCard() {
  const [rows, setRows] = useState<QueueRow[] | null>(null);
  const [summary, setSummary] = useState<QueueSummary | null>(null);
  const [error, setError] = useState(false);
  const [removing, setRemoving] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await listApplications({ queued: true, limit: 50 });
      setRows(res.applications as QueueRow[]);
      setSummary((res.queue_summary as QueueSummary | null | undefined) ?? null);
      setError(false);
    } catch {
      setError(true);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- existing working effect; refactor tracked
    void load();
  }, [load]);

  async function remove(id: number) {
    setRemoving(id);
    try {
      await recordApplicationEvent(id, { event_type: "unqueued", payload: { reason: "user_removed" } });
      await load();
    } catch (err) {
      toast.error(apiErrorMessage(err, "Could not remove this from the batch."));
    } finally {
      setRemoving(null);
    }
  }

  return (
    <section
      aria-labelledby="batch-queue"
      data-testid="batch-queue"
      className="flex flex-col gap-3 rounded-xl border border-border bg-card p-5 shadow-card"
    >
      <h3 id="batch-queue" className={LABEL}>
        Queue
      </h3>
      {error && rows === null ? (
        <div role="alert" className="flex items-center gap-3 text-sm text-destructive">
          <span>Could not load your batch.</span>
          <button
            type="button"
            data-testid="batch-queue-retry"
            onClick={() => void load()}
            className="text-xs font-medium underline-offset-2 hover:underline"
          >
            Try again
          </button>
        </div>
      ) : rows === null ? (
        <p className="text-sm text-muted-foreground" data-testid="batch-queue-loading">
          Loading…
        </p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="batch-queue-empty">
          Your batch is empty. Bring a job and leave Add to batch ticked, or let your AI hunt: the jobs it finds come here.
        </p>
      ) : (
        <>
          {queueSummaryLine(summary) && (
            <p className="font-mono text-xs text-muted-foreground" data-testid="batch-queue-summary">
              {queueSummaryLine(summary)}
            </p>
          )}
          <ul className="flex flex-col">
            {rows.map((r) => {
              const problems = preflightProblems(r.preflight);
              const name = [r.job_title || "Untitled role", r.job_company].filter(Boolean).join(" · ");
              return (
                <li
                  key={r.id}
                  data-testid={`batch-row-${r.id}`}
                  className="flex items-start justify-between gap-3 border-t border-border py-2.5 text-sm first:border-t-0"
                >
                  <div className="min-w-0 flex-1">
                    <Link
                      href={`/applications/${r.id}`}
                      className="break-words font-medium underline-offset-2 hover:underline"
                    >
                      {typeof r.queue_position === "number" && (
                        <span className="mr-1.5 font-mono text-xs text-faint">#{r.queue_position}</span>
                      )}
                      {name}
                    </Link>
                    {originLabel(r.origin) && (
                      <span
                        data-testid={`batch-row-origin-${r.id}`}
                        className="ml-2 rounded-full bg-primary/10 px-2 py-0.5 font-mono text-[10.5px] text-brand"
                      >
                        {originLabel(r.origin)}
                      </span>
                    )}
                    {problems.length > 0 && (
                      <ul data-testid={`batch-row-problems-${r.id}`} className="mt-1 flex flex-col gap-0.5 text-xs text-warning">
                        {problems.map((p) => (
                          <li key={p}>{p}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                  <button
                    type="button"
                    data-testid={`batch-remove-${r.id}`}
                    disabled={removing === r.id}
                    onClick={() => void remove(r.id)}
                    className="flex-none rounded-md border border-border px-2.5 py-1 text-xs font-medium text-muted-foreground hover:text-foreground disabled:opacity-50"
                  >
                    {removing === r.id ? "Removing…" : "Remove"}
                  </button>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}
