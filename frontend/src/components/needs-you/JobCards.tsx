"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getApplication, getCvToApprove, getReadyToSend, listPeople } from "@/lib/api";
import type { ApplicationDetail, Ask, CvToApproveRow, ReadyCardData, ReadyToSend as ReadyData } from "@/lib/api";
import { PAUSE_CHANGED_EVENT, READY_CHANGED_EVENT } from "@/lib/assistant-state";
import { buildCard, groupAsks, waitSummary } from "@/lib/job-cards";
import type { JobCardData, PersonToReach } from "@/lib/job-cards";
import { jobChip, jobHeaders, sortJobs } from "@/lib/needs-you-items";
import type { Chip, JobHeader as Header, PersonJob, SortKey } from "@/lib/needs-you-items";
import { splitReady } from "@/lib/ready-to-send";
import { cn } from "@/lib/utils";
import { Skeleton } from "@/components/ui/skeleton";
import { JobCard } from "./JobCard";
import { NewConnections, newConnections } from "./NewConnections";
import { ReadyCard, isMissingAnswer } from "./ReadyCard";
import { SendAllCard } from "./SendAllCard";
import { openQuestionAsks } from "./useReadyAsks";
import { useSalaryFacts } from "./useSalaryFacts";

// Every string here (company, role, asks) is untrusted text: rendered only as React text.

/** Jobs whose full card loads per page (and per press of "Show 8 more jobs"). */
export const JOBS_PER_PAGE = 8;
/** getApplication calls in flight at once. */
const MAX_IN_FLIGHT = 8;

/** Jobs with a drafted message nobody has marked sent, and the people with no job who have one
 *  (best effort: a failed read adds none). */
async function unsentMessages(): Promise<{ jobs: PersonJob[]; cold: PersonToReach[] }> {
  try {
    const { people } = await listPeople();
    const unsent = (people ?? []).filter((p) => p.message_count > 0 && !p.last_sent);
    return { jobs: unsent.flatMap((p) => p.jobs), cold: await newConnections(unsent) };
  } catch {
    return { jobs: [], cold: [] };
  }
}

async function cvRowsToCheck(): Promise<CvToApproveRow[]> {
  try {
    return (await getCvToApprove()).items;
  } catch {
    return [];
  }
}

const cvMap = (rows: CvToApproveRow[]): Cvs =>
  new Map(rows.map((r) => [r.application_id, { artifactId: r.cv.artifact_id, version: r.cv.version }]));

/** ONE application's card, built exactly as the Needs-you page builds it (same asks filter, same CV
 *  check, same people), so the application page and Needs you can never drift. null = nothing open. */
export async function loadJobCard(detail: ApplicationDetail): Promise<JobCardData | null> {
  const open = (detail.asks ?? []).filter((a) => a.status === "open" && !a.withdrawn_at);
  const asks = groupAsks(open).byJob.get(detail.id) ?? [];
  const cvs = cvMap(await cvRowsToCheck());
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

type Slot = { status: "loading" } | { status: "ready"; card: JobCardData } | { status: "failed" };
type Cvs = Map<number, { artifactId: number; version: number }>;

/** What the page needs to know about the job list (the cards it holds, the asks it could not place). */
export type JobCardsReport = {
  /** Fully loaded cards, in list order. */
  cards: JobCardData[];
  /** Asks of jobs whose record failed to load: they show as plain questions, never break the page. */
  failed: Ask[];
  /** Jobs that need the owner (every filter; the summary line's number). */
  total: number;
  /** People with no job and an unsent drafted note (the "New connections" group; also on the summary line). */
  connections?: number;
  /** False until people to reach, CVs to check and Ready to send have answered (more jobs may join). */
  settled: boolean;
  /** Ready-to-send cards for the badge: null until that read answers, 0 when it failed. */
  readyTotal: number | null;
  /** What the page counts (countNeedsYou): the ready cards, CVs, people's jobs and the jobs dropped. */
  inputs?: { ready: ReadyCardData[]; cvRows: CvToApproveRow[]; people: PersonJob[]; dropped: number[] };
};

/** The ready-to-send part of a job's card: its label and the ReadyCard (answers, Send, Don't send). */
function ReadySection({ card, asks, paused, embedded, onChanged, salaryFacts }: {
  card: ReadyCardData; asks: Ask[]; paused: boolean; embedded: boolean; onChanged: () => void;
  salaryFacts: ReturnType<typeof useSalaryFacts>;
}) {
  return (
    <section data-testid={`ready-section-${card.application_id}`} className="flex flex-col gap-2">
      <h3 className={cn("font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint", paused && "opacity-70")}>
        {paused ? "Ready to send · on hold while paused" : "Ready to send"}
      </h3>
      <ReadyCard card={card} asks={asks} paused={paused} embedded={embedded} onChanged={onChanged} salaryFacts={salaryFacts} />
    </section>
  );
}

/** One card per job holding everything it needs: its ready-to-send form, its CV to approve, its
 *  questions and its people to reach (owner 2026-10-26). Only the first 8 jobs show (a placeholder
 *  while each card loads), then 8 more on each press of "Show 8 more · N left": no header-only rows. */
export function JobCards({
  open,
  onChanged,
  onReport,
  sort = "urgent",
  show = "all",
}: {
  /** The open asks; the job-tied ones are grouped here, one card per job. */
  open: Ask[];
  /** The order (Most urgent, Newest first, Oldest first). */
  sort?: SortKey;
  /** The chip chosen: only its jobs show ("other" shows only New connections). */
  show?: Chip;
  onChanged: () => Promise<void>;
  onReport?: (report: JobCardsReport) => void;
}) {
  const byJob = useMemo(() => groupAsks(open).byJob, [open]);
  // One token per load of the asks: a card is refetched when its token is out of date.
  const gen = useMemo(() => ({}), [open]); // eslint-disable-line react-hooks/exhaustive-deps
  const [people, setPeople] = useState<PersonJob[] | null>(null);
  const [cold, setCold] = useState<PersonToReach[]>([]);
  // New connections sent or skipped here: hidden at once and across reloads (Skip is not saved anywhere).
  const [doneIds, setDoneIds] = useState<Set<number>>(new Set());
  const [cvRows, setCvRows] = useState<CvToApproveRow[] | null>(null);
  const [ready, setReady] = useState<ReadyData | null>(null);
  const [readyFailed, setReadyFailed] = useState(false);
  const [readyTick, setReadyTick] = useState(0);
  const [limit, setLimit] = useState(JOBS_PER_PAGE);
  const [slots, setSlots] = useState<Map<number, Slot>>(new Map());
  const [limiter] = useState(() => makeLimiter(MAX_IN_FLIGHT));
  const fetched = useRef(new Map<number, object>());
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  // Someone-to-reach jobs and CVs to check: read after the first paint, never in its way.
  useEffect(() => {
    let live = true;
    void unsentMessages().then((p) => {
      if (!live) return;
      setPeople(p.jobs);
      setCold(p.cold);
    });
    void cvRowsToCheck().then((r) => live && setCvRows(r));
    return () => {
      live = false;
    };
  }, [gen]);

  useEffect(() => {
    let live = true;
    Promise.resolve().then(() => getReadyToSend()).then(
      (d) => {
        if (!live) return;
        setReady(d);
        setReadyFailed(false);
      },
      () => live && setReadyFailed(true),
    );
    return () => {
      live = false;
    };
  }, [gen, readyTick]);

  useEffect(() => {
    const onPause = () => setReadyTick((t) => t + 1);
    window.addEventListener(PAUSE_CHANGED_EVENT, onPause);
    return () => window.removeEventListener(PAUSE_CHANGED_EVENT, onPause);
  }, []);

  // After any send or decline: re-read this list, the asks, and tell the morning check on Home.
  const readyChanged = useCallback(() => {
    setReadyTick((t) => t + 1);
    window.dispatchEvent(new CustomEvent(READY_CHANGED_EVENT));
    void onChanged();
  }, [onChanged]);

  const salaryFacts = useSalaryFacts(!!ready?.items.some((c) => c.answers.some(isMissingAnswer)));

  const headers: Header[] = useMemo(
    () => sortJobs(jobHeaders(open, ready?.items ?? [], cvRows ?? [], people ?? []), sort),
    [open, people, ready, cvRows, sort],
  );

  // A loaded card takes this header's current CV; it stays while anything on it is open.
  const cardOf = useCallback((h: Header): JobCardData | null => {
    const s = slots.get(h.id);
    return s?.status === "ready" ? { ...s.card, cv: h.cv } : null;
  }, [slots]);
  const live = useCallback((h: Header) => {
    const s = slots.get(h.id);
    if (s?.status === "failed") return h.ready !== null;
    const c = cardOf(h);
    return !c || c.asks.length > 0 || c.people.length > 0 || h.ready !== null || h.cv !== null;
  }, [slots, cardOf]);

  const alive = useMemo(() => headers.filter(live), [headers, live]);
  // The chosen chip's jobs; "Other" holds no job (only New connections show from here).
  const shown = useMemo(
    () => (show === "all" ? alive : show === "other" ? [] : alive.filter((h) => jobChip(h) === show)),
    [alive, show],
  );
  // The shown jobs: a card that loads with nothing open drops out and the next job takes its place.
  const page = useMemo(() => shown.slice(0, limit), [shown, limit]);
  // Load the full card of every job on a shown page, at most MAX_IN_FLIGHT at a time.
  useEffect(() => {
    const set = (id: number, slot: Slot, onlyIfAbsent = false) =>
      setSlots((m) => (onlyIfAbsent && m.has(id) ? m : new Map(m).set(id, slot)));
    for (const { id } of page) {
      if (fetched.current.get(id) === gen) continue;
      fetched.current.set(id, gen);
      set(id, { status: "loading" }, true);
      void limiter(() => getApplication(id)).then(
        (d) => mounted.current && fetched.current.get(id) === gen && set(id, { status: "ready", card: buildCard(d, byJob.get(id) ?? [], null) }),
        () => mounted.current && fetched.current.get(id) === gen && set(id, { status: "failed" }),
      );
    }
  }, [page, gen, limiter, byJob]);

  const cards = useMemo(() => headers.flatMap((h) => cardOf(h) ?? []), [headers, cardOf]);
  const failed = useMemo(
    () => headers.flatMap((h) => (slots.get(h.id)?.status === "failed" ? h.asks.filter((a) => !h.ready || !a.questions?.length) : [])),
    [headers, slots],
  );
  const connections = useMemo(() => cold.filter((p) => !doneIds.has(p.contact.id)), [cold, doneIds]);
  const settled = people !== null && cvRows !== null && (ready !== null || readyFailed);
  const dropped = useMemo(() => headers.filter((h) => !live(h)).map((h) => h.id), [headers, live]);
  useEffect(() => {
    onReport?.({
      cards, failed, total: alive.length, connections: connections.length, settled,
      readyTotal: ready ? ready.total : readyFailed ? 0 : null,
      inputs: { ready: ready?.items ?? [], cvRows: cvRows ?? [], people: people ?? [], dropped },
    });
  }, [onReport, cards, failed, alive.length, connections.length, settled, ready, readyFailed, cvRows, people, dropped]);

  const { clean, held } = useMemo(() => {
    const asked = new Set(openQuestionAsks(open).map((a) => a.application_id));
    const s = splitReady(ready?.items ?? []);
    // A job with an unanswered open ask never goes in "Send all": it is answered on its own card.
    return { clean: s.clean.filter((c) => !asked.has(c.application_id)), held: s.held };
  }, [ready, open]);

  const readyError = readyFailed && !ready && (
    <div role="alert" className="flex items-center gap-3 text-sm text-destructive">
      <span>Could not load Ready to send.</span>
      <button type="button" data-testid="ready-retry" onClick={() => setReadyTick((t) => t + 1)} className="text-xs font-medium underline-offset-2 hover:underline">
        Try again
      </button>
    </div>
  );
  const showPeople = show === "all" || show === "other";
  if (shown.length === 0 && (connections.length === 0 || !showPeople)) return readyError || null;
  const paused = ready?.paused ?? false;
  const readyPart = (h: Header, embedded: boolean) =>
    h.ready && <ReadySection card={h.ready} asks={h.asks} paused={paused} embedded={embedded} onChanged={readyChanged} salaryFacts={salaryFacts} />;
  const pending = shown.length - page.length;
  const next = Math.min(JOBS_PER_PAGE, pending);
  return (
    <div className="flex flex-col gap-3">
      {readyError}
      {clean.length >= 2 && (show === "all" || show === "ready") && <SendAllCard clean={clean} held={held} paused={paused} onChanged={readyChanged} />}
      {page.length > 0 && <ul className="flex flex-col gap-3" data-testid="job-cards">
        {page.map((h) => {
          const slot = slots.get(h.id);
          const card = cardOf(h);
          if (card) return <JobCard key={h.id} card={card} onChanged={onChanged} ready={readyPart(h, true)} />;
          if (slot?.status === "failed") return <li key={h.id}>{readyPart(h, false)}</li>;
          const title = [h.company, h.role].filter(Boolean).join(" · ") || `Job ${h.id}`;
          const what = [h.ready && "Ready to send", h.cv && "Approve CV", (h.asks.length > 0 || (!h.ready && !h.cv)) && waitSummary(h.asks)];
          return (
            <li key={h.id} data-testid={`job-header-${h.id}`} className="flex flex-col gap-2 rounded-xl border border-border bg-card p-4 shadow-card">
              <div className="flex items-baseline justify-between gap-3">
                <Link href={`/applications/${h.id}`} data-testid={`title-link-${h.id}`} className="font-medium underline-offset-2 hover:underline">
                  {title}
                </Link>
                <span className="text-xs text-muted-foreground">{what.filter(Boolean).join(" · ")}</span>
              </div>
              {slot?.status === "loading" && <Skeleton data-testid={`job-skeleton-${h.id}`} className="h-16 w-full" />}
            </li>
          );
        })}
      </ul>}
      {pending > 0 && (
        <button
          type="button"
          data-testid="more-jobs"
          onClick={() => setLimit((n) => n + JOBS_PER_PAGE)}
          className="w-full rounded-lg border border-border bg-card px-3 py-2 text-sm font-medium hover:bg-muted"
        >
          Show {next} more · {pending} left
        </button>
      )}
      {showPeople && <NewConnections people={connections} onChanged={onChanged}
        onDone={(id) => setDoneIds((s) => new Set(s).add(id))} />}
    </div>
  );
}
