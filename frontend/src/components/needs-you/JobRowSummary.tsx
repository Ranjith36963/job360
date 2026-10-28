"use client";

import Link from "next/link";
import type { JobCardData } from "@/lib/job-cards";
import { hasFormHandoff, waitSummary } from "@/lib/job-cards";
import { cameFrom, nextStep, oldestAt, type RowFacts } from "@/lib/job-row";
import type { JobHeader } from "@/lib/needs-you-items";
import { heldText } from "@/lib/ready-to-send";
import { cn, relativeTime } from "@/lib/utils";

// A job's folded row on Needs you: one scannable status line (owner 2026-10-27). Every value is
// untrusted text (an assistant or a job site wrote it): React text only, never HTML.

/** The row's columns on a wide screen; below `lg` the cells wrap into two lines. */
const COLS = "lg:grid lg:grid-cols-[minmax(0,1.8fr)_minmax(0,1.9fr)_4.5rem_minmax(0,1fr)_minmax(0,1.4fr)_3.5rem] lg:items-center lg:gap-x-4";
// An empty cell keeps its column on a wide screen and takes no room when the line wraps.
const CELL = "min-w-0 max-lg:empty:hidden lg:truncate";
const HEAD = "font-mono text-[10.5px] font-medium uppercase tracking-[0.09em] text-faint";

/** The column names above the rows, wide screens only (lined up with the toggle and the action slot). */
export function JobRowHeader() {
  return (
    <div aria-hidden className="hidden items-center gap-1 pr-3 pl-1 lg:flex" data-testid="job-row-header">
      <span className="w-11 shrink-0" />
      <div className={cn("min-w-0 flex-1", COLS)}>
        {["Job", "Next step", "Waiting", "Where", "Came from", "Fit"].map((h) => <span key={h} className={HEAD}>{h}</span>)}
      </div>
      <span className="w-40 shrink-0" />
    </div>
  );
}

const TONE: Record<string, string> = {
  "Form open now": "bg-warning-soft text-warning",
  "Ready to send": "bg-brand-soft text-brand",
};

/** What is inside the card, in plain words: "Ready to send · Approve CV · Sign in · Check 2 answers". */
export function whatWaits(h: JobHeader, card: JobCardData | null): string {
  const what = [h.ready && "Ready to send", h.cv && "Approve CV",
    (h.asks.length > 0 || (!h.ready && !h.cv)) && waitSummary(h.asks, card ? card.people.length : undefined)];
  const held = heldText(h.ready?.held_seconds ?? Math.max(0, ...h.asks.map((a) => a.held_seconds ?? 0)));
  // The chip already names the group: "Ready to send" is not said twice.
  return [...what.filter((w) => w && w !== nextStep(h)), held.replace(/^ · /, "")].filter(Boolean).join(" · ");
}

export function JobRowSummary({ h, card, facts, linkTestid }: {
  h: JobHeader;
  card: JobCardData | null;
  facts: RowFacts;
  /** The title link's test id (the loading row keeps the old `title-link-<id>`). */
  linkTestid: string;
}) {
  const company = card?.company || h.company;
  const role = card?.role || h.role;
  const step = nextStep(h);
  const since = oldestAt(h);
  const from = cameFrom(facts);
  return (
    <div className={cn("flex min-w-0 flex-col gap-1", COLS)}>
      <Link href={`/applications/${h.id}`} data-testid={linkTestid} className="min-w-0 truncate text-sm underline-offset-2 hover:underline">
        <span className="font-semibold">{company || "Unknown company"}</span>{" "}
        <span className="text-muted-foreground">{role || "Untitled role"}</span>
      </Link>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground lg:contents">
        <span className={cn(CELL, "flex items-center gap-2 max-lg:basis-full")}>
          <span data-testid={`job-step-${h.id}`}
            className={cn("shrink-0 rounded px-1.5 py-0.5 text-[11px] font-medium", TONE[step] ?? "bg-muted text-foreground")}>
            {step}
          </span>
          <span className="min-w-0 lg:truncate" title={whatWaits(h, card)} data-testid={`job-what-${h.id}`}>{whatWaits(h, card)}</span>
        </span>
        {since && <span className={CELL} title="Waiting since"><span className="lg:hidden">since </span>{relativeTime(since)}</span>}
        <span className={CELL}>{facts.location}</span>
        <span className={CELL} title={from ? `Brought ${from}` : undefined} data-testid={`job-from-${h.id}`}>{from}</span>
        <span className={cn(CELL, "font-mono")}>{facts.score != null && `Fit ${facts.score}`}</span>
      </div>
    </div>
  );
}

const ACTION = "rounded-md border border-border px-2.5 py-1.5 text-xs font-medium whitespace-nowrap hover:bg-muted";

/** The one action a folded row offers without unfolding: a held form's "Finish it here" (it opens the
 *  card and starts the live view there, the same button), else a CAPTCHA / sign-in step's form link. */
export function JobRowAction({ card, onOpen }: { card: JobCardData | null; onOpen: () => void }) {
  if (!card) return null;
  const held = card.asks.find(({ ask, kind }) => hasFormHandoff(kind) && ask.finish_here === true && (ask.held_seconds ?? 0) > 0);
  if (held)
    return (
      <button type="button" className={cn(ACTION, "border-transparent bg-primary text-primary-foreground hover:bg-primary/90")}
        data-testid={`row-finish-${card.applicationId}`}
        onClick={() => {
          onOpen();
          document.querySelector<HTMLButtonElement>(`[data-testid="finish-here-${held.ask.id}"]`)?.click();
        }}>
        Finish it here
      </button>
    );
  if (card.formUrl && card.asks.some(({ kind }) => hasFormHandoff(kind)))
    return (
      <a href={card.formUrl} target="_blank" rel="noopener noreferrer" className={ACTION} data-testid={`row-form-${card.applicationId}`}>
        Open the form ↗
      </a>
    );
  return null;
}
