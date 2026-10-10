"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { getMorningCheck } from "@/lib/api";
import type { MorningCheck as MorningCheckData } from "@/lib/api";
import { PAUSE_CHANGED_EVENT } from "@/lib/assistant-state";
import { formatFeedTime, hasStamp, readLastVisit, writeLastVisit } from "@/lib/home";
import {
  APPLY_MODE_LABEL,
  BUCKETS,
  BUCKET_LABEL,
  CHECK_LOOKBACK_DAYS,
  LAST_CHECK_KEY,
  SUBMIT_MODE_LABEL,
  pausedByText,
  quotaText,
  sinceText,
} from "@/lib/morning-check";
import type { Bucket } from "@/lib/morning-check";
import { cn } from "@/lib/utils";

// Company, title and every label here are written by the user's assistants -
// untrusted. They are only ever rendered as React text nodes.

const sectionLabel = "font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint";
const linkBtn = "text-xs font-medium text-muted-foreground underline-offset-2 hover:text-foreground hover:underline";

/**
 * The top of "Needs you" (owner decision 2026-10-09): are the assistants
 * running, under which settings, how much of today's limit is used - then what
 * happened since the user last looked: sent, blocked, waiting, failed.
 */
export function MorningCheck() {
  const [data, setData] = useState<MorningCheckData | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState<Bucket | null>(null);
  // One window per page view: a retry or a pause toggle keeps it. `last` is null on a first visit.
  // The words show the server's `since`: it clamps a stale (>30 days) or future stamp.
  const [win] = useState(() => {
    const since = readLastVisit(new Date(), LAST_CHECK_KEY, CHECK_LOOKBACK_DAYS);
    return { since, last: hasStamp(LAST_CHECK_KEY) ? since : null };
  });
  const stamped = useRef(false);

  const load = useCallback(() => {
    getMorningCheck(win.since).then(
      (d) => {
        setData(d);
        setFailed(false);
      },
      () => setFailed(true),
    );
  }, [win]);

  useEffect(() => {
    load();
  }, [load]);

  // A pause or resume from the top bar updates the strip at once.
  useEffect(() => {
    const onPause = () => load();
    window.addEventListener(PAUSE_CHANGED_EVENT, onPause);
    return () => window.removeEventListener(PAUSE_CHANGED_EVENT, onPause);
  }, [load]);

  // The stamp moves forward only after the tally has been shown.
  useEffect(() => {
    if (data && !stamped.current) {
      stamped.current = true;
      writeLastVisit(data.now, LAST_CHECK_KEY);
    }
  }, [data]);

  const heading = (
    <h2 id="morning-check" className={sectionLabel}>
      Morning check
    </h2>
  );

  if (!data) {
    return (
      <section aria-labelledby="morning-check" className="flex flex-col gap-2.5">
        {heading}
        {failed ? (
          <div role="alert" className="flex items-center gap-3 text-sm text-destructive">
            <span>Could not load the morning check.</span>
            <button type="button" data-testid="mc-retry" onClick={() => load()} className={linkBtn}>
              Try again
            </button>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">Loading…</p>
        )}
      </section>
    );
  }

  const { state, tally } = data;
  const sep = <span aria-hidden="true" className="text-faint">·</span>;
  return (
    <section aria-labelledby="morning-check" data-testid="morning-check" className="flex flex-col gap-2.5">
      {heading}
      <div
        data-testid="mc-strip"
        className="flex flex-wrap items-center gap-x-[18px] gap-y-2 rounded-xl border border-border bg-card px-4 py-3 shadow-card"
      >
        <span className="inline-flex items-center gap-2 font-medium">
          <i
            aria-hidden="true"
            className={cn(
              "block h-2 w-2 rounded-full",
              state.paused ? "bg-warning" : "bg-primary shadow-[0_0_0_3px_var(--brand-soft)]",
            )}
          />
          {state.paused ? "Paused" : "Running"}
        </span>
        {sep}
        <span className="text-[13.5px] text-muted-foreground">{APPLY_MODE_LABEL[state.apply_mode] ?? state.apply_mode}</span>
        {sep}
        <span className="text-[13.5px] text-muted-foreground">{SUBMIT_MODE_LABEL[state.submit_mode] ?? state.submit_mode}</span>
        <span className="ml-auto font-mono text-[12.5px] font-medium">{quotaText(state.applied_today, state.daily_cap)}</span>
      </div>
      {state.paused && state.paused_by && state.paused_at && (
        <p data-testid="mc-paused-by" className="px-1 font-mono text-xs text-faint">
          {pausedByText(state.paused_by, state.paused_at)}
        </p>
      )}
      <div data-testid="mc-tally" className="flex flex-wrap items-baseline gap-x-[22px] gap-y-1.5 px-1 text-sm">
        <span className="mr-1 font-mono text-xs text-faint">{sinceText(win.last ? data.since : null)}</span>
        {BUCKETS.map((b) => {
          const count = tally[b].count;
          const body = (
            <>
              {BUCKET_LABEL[b]} <span className="font-mono font-medium">{count}</span>
            </>
          );
          return count > 0 ? (
            <button
              key={b}
              type="button"
              data-testid={`tally-${b}`}
              aria-expanded={open === b}
              onClick={() => setOpen(open === b ? null : b)}
              className="min-h-11 text-foreground underline decoration-brand decoration-[1.5px] underline-offset-[3px] md:min-h-0"
            >
              {body}
            </button>
          ) : (
            <span key={b} className="text-muted-foreground">
              {body}
            </span>
          );
        })}
      </div>
      {open && tally[open].count > 0 && (
        <ul data-testid={`tally-list-${open}`} className="flex flex-col divide-y divide-border rounded-xl border border-border bg-card">
          {tally[open].items.map((it) => {
            const href = open === "sent" && it.receipt_id != null ? `/receipts/${it.receipt_id}` : `/applications/${it.application_id}`;
            return (
              <li key={`${it.application_id}-${it.at}`}>
                <Link href={href} className="flex min-h-11 flex-wrap items-baseline gap-x-3 px-4 py-2 text-sm hover:bg-muted">
                  <span>{[it.company, it.title].filter(Boolean).join(" · ") || "An application"}</span>
                  <span className="font-mono text-xs text-faint">{formatFeedTime(it.at)}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
