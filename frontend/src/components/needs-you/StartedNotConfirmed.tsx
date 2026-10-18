"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { SubmitUnconfirmedBanner } from "@/components/applications/SubmitUnconfirmedBanner";
import { fetchWhatsNewPages } from "@/lib/home-fetch";
import { submitUnconfirmed } from "@/lib/application-actions";
import { relativeTime } from "@/lib/utils";

const DAYS = 30;
const PAGES = 3;
const sectionLabel = "font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint";

export type StartedRow = { id: number; title: string; company: string; at: string };

/** Applications whose newest submit event is "started" (nothing confirmed or marked
 *  not sent after it). Derived from the events the what's-new feed already carries:
 *  the backend has no list for it, so only the last 30 days are looked at. */
type FeedEvent = {
  id: number;
  application_id: number;
  event_type: string;
  occurred_at: string;
  payload: Record<string, unknown>;
  corrects_event_id: number | null;
};

export function startedNotConfirmed(
  events: readonly FeedEvent[],
  apps: { id: number; job_title: string; job_company: string }[],
): StartedRow[] {
  const byApp = new Map<number, FeedEvent[]>();
  for (const e of events) {
    const list = byApp.get(e.application_id) ?? [];
    list.push(e);
    byApp.set(e.application_id, list);
  }
  const rows: StartedRow[] = [];
  for (const [id, list] of byApp) {
    if (!submitUnconfirmed(list)) continue;
    const a = apps.find((x) => x.id === id);
    const started = list.filter((e) => e.event_type === "submit_attempted").sort((x, y) => y.id - x.id)[0];
    rows.push({ id, title: a?.job_title || "Untitled role", company: a?.job_company ?? "", at: started?.occurred_at ?? "" });
  }
  return rows.sort((x, y) => Date.parse(y.at) - Date.parse(x.at));
}

/** "Started, not confirmed" on Needs you. Best effort: a failed read shows nothing. */
export function StartedNotConfirmed() {
  const [rows, setRows] = useState<StartedRow[]>([]);

  const fetchRows = useCallback(async (): Promise<StartedRow[]> => {
    try {
      const since = new Date(Date.now() - DAYS * 86_400_000).toISOString();
      const feed = await fetchWhatsNewPages(since, PAGES);
      return startedNotConfirmed(feed.events, feed.applications);
    } catch {
      return [];
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void fetchRows().then((r) => {
      if (!cancelled) setRows(r);
    });
    return () => {
      cancelled = true;
    };
  }, [fetchRows]);

  const refresh = useCallback(async () => setRows(await fetchRows()), [fetchRows]);

  if (rows.length === 0) return null;
  return (
    <section
      aria-labelledby="started-not-confirmed"
      data-testid="started-not-confirmed"
      className="flex flex-col gap-3 rounded-xl border border-border bg-card p-5 shadow-card"
    >
      <h2 id="started-not-confirmed" className={sectionLabel}>
        Started, not confirmed
      </h2>
      <ul className="flex flex-col gap-3">
        {rows.map((r) => (
          <li
            key={r.id}
            data-testid={`started-${r.id}`}
            className="flex flex-col gap-1 rounded-xl border border-warning/40 bg-warning/10 p-4 text-sm"
          >
            <Link href={`/applications/${r.id}`} className="font-medium underline-offset-2 hover:underline">
              {[r.title, r.company].filter(Boolean).join(" · ")}
            </Link>
            <p className="text-muted-foreground">
              Your assistant started submitting this and it was never confirmed. Check the employer&apos;s email or site.
              {r.at && <span className="font-mono"> {relativeTime(r.at)}</span>}
            </p>
            <SubmitUnconfirmedBanner applicationId={r.id} compact idSuffix={`-${r.id}`} onChanged={refresh} />
          </li>
        ))}
      </ul>
    </section>
  );
}
