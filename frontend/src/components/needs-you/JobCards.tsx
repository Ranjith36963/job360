"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { getApplication, getCvToApprove, listPeople } from "@/lib/api";
import type { ApplicationDetail, Ask } from "@/lib/api";
import { buildCard, groupAsks, isBlocker, rowKind } from "@/lib/job-cards";
import type { JobCardData } from "@/lib/job-cards";
import { Skeleton } from "@/components/ui/skeleton";
import { JobCard } from "./JobCard";

// Every string here (company, role, asks) is untrusted text: rendered only as React text.

/** Jobs whose full card loads per page (and per press of "Show 8 more jobs"). */
export const JOBS_PER_PAGE = 8;
/** getApplication calls in flight at once. */
const MAX_IN_FLIGHT = 8;

type PersonJob = { application_id: number; job_company: string; job_title: string };

/** Jobs with a drafted message nobody has marked sent (best effort: a failed read adds none). */
async function jobsWithUnsentMessages(): Promise<PersonJob[]> {
  try {
    const { people } = await listPeople();
    return (people ?? []).filter((p) => p.message_count > 0 && !p.last_sent).flatMap((p) => p.jobs);
  } catch {
    return [];
  }
}

async function cvsToCheck(): Promise<Map<number, { artifactId: number; version: number }>> {
  try {
    const { items } = await getCvToApprove();
    return new Map(items.map((r) => [r.application_id, { artifactId: r.cv.artifact_id, version: r.cv.version }]));
  } catch {
    return new Map();
  }
}

/** ONE application's card, built exactly as the Needs-you page builds it (same asks filter, same CV
 *  check, same people), so the application page and Needs you can never drift. null = nothing open. */
export async function loadJobCard(detail: ApplicationDetail): Promise<JobCardData | null> {
  const open = (detail.asks ?? []).filter((a) => a.status === "open" && !a.withdrawn_at);
  const asks = groupAsks(open).byJob.get(detail.id) ?? [];
  const cvs = await cvsToCheck();
  const card = buildCard(detail, asks, cvs.get(detail.id) ?? null);
  return card.asks.length > 0 || card.people.length > 0 ? card : null;
}

/** At most `max` thunks run at once; the rest wait their turn. */
function makeLimiter(max: number) {
  let running = 0;
  const queue: (() => void)[] = [];
  const pump = () => {
    while (running < max && queue.length > 0) {
      running++;
      queue.shift()!();
    }
  };
  return <T,>(fn: () => Promise<T>): Promise<T> =>
    new Promise<T>((resolve, reject) => {
      queue.push(() => {
        fn()
          .then(resolve, reject)
          .finally(() => {
            running--;
            pump();
          });
      });
      pump();
    });
}

type Slot = { status: "loading" } | { status: "ready"; card: JobCardData } | { status: "failed" } | { status: "dropped" };
type Cvs = Map<number, { artifactId: number; version: number }>;

/** What the page needs to know about the job list (the cards it holds, the asks it could not place). */
export type JobCardsReport = {
  /** Fully loaded cards, in list order. */
  cards: JobCardData[];
  /** Asks of jobs whose record failed to load: they show as plain questions, never break the page. */
  failed: Ask[];
  /** Jobs listed (headers, skeletons and cards). */
  total: number;
  /** False until the people-to-reach read has answered (more jobs may still join). */
  settled: boolean;
};

type Header = { id: number; company: string; role: string; waiting: number };

/** With only the asks to go on: a blocker first, then questions, then the rest; oldest first. */
function headerRank(asks: Ask[]): number {
  return asks.some((a) => isBlocker(rowKind(a))) ? 0 : 1;
}

/** Fast first paint: a header per job from the asks alone, the full card (getApplication) only for the
 *  first 8, then 8 more on each press of "Show 8 more jobs" (owner-driven, so a tall screen never
 *  loads every job at once). */
export function JobCards({
  open,
  onChanged,
  onReport,
}: {
  /** The open asks; the job-tied ones are grouped here, one card per job. */
  open: Ask[];
  onChanged: () => Promise<void>;
  onReport?: (report: JobCardsReport) => void;
}) {
  const byJob = useMemo(() => groupAsks(open).byJob, [open]);
  // One token per load of the asks: a card is refetched when its token is out of date.
  const gen = useMemo(() => ({}), [open]); // eslint-disable-line react-hooks/exhaustive-deps
  const [people, setPeople] = useState<PersonJob[] | null>(null);
  const [limit, setLimit] = useState(JOBS_PER_PAGE);
  const [slots, setSlots] = useState<Map<number, Slot>>(new Map());
  const [limiter] = useState(() => makeLimiter(MAX_IN_FLIGHT));
  const fetched = useRef(new Map<number, object>());
  const cvsRef = useRef<{ gen: object; p: Promise<Cvs> } | null>(null);
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  // Someone-to-reach jobs: read after the first paint, never in its way.
  useEffect(() => {
    let live = true;
    void jobsWithUnsentMessages().then((p) => {
      if (live) setPeople(p);
    });
    return () => {
      live = false;
    };
  }, [gen]);

  const headers: Header[] = useMemo(() => {
    const out: (Header & { rank: number; oldest: string })[] = [];
    for (const [id, asks] of byJob) {
      const first = asks[0];
      out.push({
        id,
        company: first?.job_company ?? "",
        role: first?.job_title ?? "",
        waiting: asks.length,
        rank: headerRank(asks),
        oldest: asks.map((a) => a.asked_at).sort()[0] ?? "",
      });
    }
    out.sort((a, b) => a.rank - b.rank || a.oldest.localeCompare(b.oldest));
    const seen = new Set(byJob.keys());
    const reach: Header[] = [];
    for (const j of people ?? []) {
      if (seen.has(j.application_id)) continue;
      seen.add(j.application_id);
      reach.push({ id: j.application_id, company: j.job_company, role: j.job_title, waiting: 0 });
    }
    return [...out, ...reach];
  }, [byJob, people]);

  // Load the full card of every job on a shown page, at most MAX_IN_FLIGHT at a time.
  useEffect(() => {
    const cvsFor = (): Promise<Cvs> => {
      if (cvsRef.current?.gen !== gen) cvsRef.current = { gen, p: cvsToCheck() };
      return cvsRef.current.p;
    };
    const set = (id: number, slot: Slot, onlyIfAbsent = false) =>
      setSlots((m) => (onlyIfAbsent && m.has(id) ? m : new Map(m).set(id, slot)));
    for (const { id } of headers.slice(0, limit)) {
      if (fetched.current.get(id) === gen) continue;
      fetched.current.set(id, gen);
      set(id, { status: "loading" }, true);
      const detail = limiter(() => getApplication(id)).then((d) => d, () => null);
      void Promise.all([detail, cvsFor()]).then(([d, cvs]) => {
        if (!mounted.current || fetched.current.get(id) !== gen) return;
        if (!d) return set(id, { status: "failed" });
        const card = buildCard(d, byJob.get(id) ?? [], cvs.get(d.id) ?? null);
        set(id, card.asks.length > 0 || card.people.length > 0 ? { status: "ready", card } : { status: "dropped" });
      });
    }
  }, [headers, limit, gen, limiter, byJob]);

  const shown = headers.filter((h) => {
    const s = slots.get(h.id);
    return s?.status !== "failed" && s?.status !== "dropped";
  });
  const cards = useMemo(
    () => headers.flatMap((h) => (slots.get(h.id)?.status === "ready" ? [(slots.get(h.id) as { card: JobCardData }).card] : [])),
    [headers, slots],
  );
  const failed = useMemo(
    () => headers.flatMap((h) => (slots.get(h.id)?.status === "failed" ? (byJob.get(h.id) ?? []) : [])),
    [headers, slots, byJob],
  );
  const settled = people !== null;
  useEffect(() => {
    onReport?.({ cards, failed, total: shown.length, settled });
  }, [onReport, cards, failed, shown.length, settled]);

  if (shown.length === 0) return null;
  const pending = headers.length - Math.min(limit, headers.length);
  const next = Math.min(JOBS_PER_PAGE, pending);
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-muted-foreground" data-testid="jobs-count">
        {shown.length} {shown.length === 1 ? "job" : "jobs"} waiting for you
      </p>
      <ul className="flex flex-col gap-3" data-testid="job-cards">
        {shown.map((h) => {
          const slot = slots.get(h.id);
          if (slot?.status === "ready") return <JobCard key={h.id} card={slot.card} onChanged={onChanged} />;
          const title = [h.company, h.role].filter(Boolean).join(" · ") || `Job ${h.id}`;
          return (
            <li key={h.id} data-testid={`job-header-${h.id}`} className="flex flex-col gap-2 rounded-xl border border-border bg-card p-4 shadow-card">
              <div className="flex items-baseline justify-between gap-3">
                <Link href={`/applications/${h.id}`} className="font-medium underline-offset-2 hover:underline">
                  {title}
                </Link>
                <span className="text-xs text-muted-foreground">
                  {h.waiting > 0 ? `${h.waiting} ${h.waiting === 1 ? "thing waits" : "things wait"}` : "someone to reach"}
                </span>
              </div>
              {slot?.status === "loading" && <Skeleton data-testid={`job-skeleton-${h.id}`} className="h-16 w-full" />}
            </li>
          );
        })}
      </ul>
      {headers.length > limit && (
        <div>
          <button
            type="button"
            data-testid="more-jobs"
            onClick={() => setLimit((n) => n + JOBS_PER_PAGE)}
            className="text-xs font-medium text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
          >
            Show {next} more jobs
          </button>
        </div>
      )}
    </div>
  );
}
