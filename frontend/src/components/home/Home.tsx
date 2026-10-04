"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import type { ReactNode } from "react";
import { getStats, getWhatsNew, listApplications, listAsks } from "@/lib/api";
import type { ApplicationSummary, Ask, Stats, WhatsNew, WhatsNewEvent } from "@/lib/api";
import { whoLabel } from "@/lib/event-labels";
import { formatDayMonth } from "@/lib/format-date";
import { AskCard, announce } from "@/components/needs-you/NeedsYou";
import { describeEvent } from "./describe-event";

// ---------------------------------------------------------------------------
// The signed-in home (redesign slice 2): a reading pane (lede, Needs you, the
// applications ledger) and a right rail (counts, due, what the assistant did).
//
// Every section loads on its own: one failed call shows one neutral line in
// that section and the rest of the page still renders. Empty sections stay
// silent (rule #29). Job360 never judges: counts only, no rates, no cheer.
// Every string from the API (company, detail, question, assistant name) is
// untrusted and only ever rendered as a React text node.
// ---------------------------------------------------------------------------

type Load<T> = { state: "loading" } | { state: "error" } | { state: "ok"; data: T };

const LOADING = { state: "loading" } as const;
const ERROR_LINE = "Couldn't load this — refresh to try again.";

/** When the user last opened home: the server's `now` from that visit. */
const LAST_SEEN_KEY = "job360:home:last-seen";
/** This tab session's baseline, pinned on the first home load of the session. */
const BASELINE_KEY = "job360:home:baseline";

const WEEK_MS = 7 * 24 * 60 * 60 * 1000;
const WHATS_NEW_PAGE = 200; // the route's max `limit`
const WHATS_NEW_MAX_PAGES = 10;
const LEDGER_ROWS = 8;
const HOME_ASKS = 3;
const FEED_ROWS = 6;

/**
 * "Since you were last here" needs the PREVIOUS visit, but we also have to
 * record THIS visit. Writing last-seen straight away would make a refresh
 * count from a moment ago (and say "nothing new"). So the first home load in
 * a tab session pins the old value in sessionStorage, and every load in that
 * session (refreshes included) counts from the pinned value. That makes it
 * safe to write last-seen as soon as the counts are in: the next SESSION
 * counts from here. Every storage access can throw (private mode, blocked
 * site data), so each one is wrapped and the page works without storage.
 */
function readBaseline(): string | null {
  try {
    const pinned = window.sessionStorage.getItem(BASELINE_KEY);
    if (pinned !== null) return pinned || null;
  } catch {
    // No sessionStorage — fall through to localStorage alone.
  }
  let last: string | null = null;
  try {
    last = window.localStorage.getItem(LAST_SEEN_KEY);
  } catch {
    last = null;
  }
  if (last && Number.isNaN(Date.parse(last))) last = null;
  try {
    window.sessionStorage.setItem(BASELINE_KEY, last ?? "");
  } catch {
    // Not pinned: a refresh will count from this visit instead. Harmless.
  }
  return last;
}

function writeLastSeen(now: string): void {
  try {
    window.localStorage.setItem(LAST_SEEN_KEY, now);
  } catch {
    // Storage blocked: next visit falls back to "the last 7 days".
  }
}

/** The server stores `recorded_at` as Python isoformat text and compares
 * `since` to it as text, so send the same shape ("+00:00", not "Z"). */
function serverIso(d: Date): string {
  return d.toISOString().replace("Z", "+00:00");
}

/** Every event since `since`, oldest first, paging until the server says
 * there is no more (bounded, so a huge backlog can't spin forever). */
async function loadAllWhatsNew(since: string): Promise<{ data: WhatsNew; complete: boolean }> {
  let page = await getWhatsNew({ since, limit: WHATS_NEW_PAGE });
  const events = [...page.events];
  const apps = new Map(page.applications.map((a) => [a.id, a]));
  let pages = 1;
  while (page.truncated && pages < WHATS_NEW_MAX_PAGES) {
    page = await getWhatsNew({
      since: page.next_since,
      after_id: page.next_after_id ?? undefined,
      limit: WHATS_NEW_PAGE,
    });
    events.push(...page.events);
    for (const a of page.applications) apps.set(a.id, a);
    pages += 1;
  }
  return {
    data: { ...page, events, applications: [...apps.values()] },
    complete: !page.truncated,
  };
}

// ---- time words ------------------------------------------------------------

const noSubscribe = () => () => {};

/** "Saturday 3 October 2026", in the viewer's locale. */
function todayLabel(): string {
  return new Date().toLocaleDateString(undefined, {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

function sameDay(a: Date, b: Date): boolean {
  return a.toDateString() === b.toDateString();
}

function clock(d: Date): string {
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

/** Ledger / feed "when": the time today, the weekday this week, else "3 Oct". */
function whenLabel(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  if (sameDay(d, now)) return clock(d);
  if (now.getTime() - d.getTime() < 6 * 24 * 60 * 60 * 1000 && d < now) {
    return d.toLocaleDateString(undefined, { weekday: "short" });
  }
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

/** "today at 14:05" / "yesterday at 14:05" / "on Tuesday at 14:05" / "on 3 Oct at 14:05". */
function dayAt(iso: string): string {
  // `applied_at` is the agent's own text: a bare date has no time to show, and
  // JS would read it as UTC midnight (a made-up clock, maybe the wrong day).
  const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(iso.trim());
  const d = dateOnly ? new Date(`${iso.trim()}T12:00:00`) : new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  let day: string;
  if (sameDay(d, now)) day = "today";
  else if (sameDay(d, yesterday)) day = "yesterday";
  else if (now.getTime() - d.getTime() < 6 * 24 * 60 * 60 * 1000 && d < now)
    day = `on ${d.toLocaleDateString(undefined, { weekday: "long" })}`;
  else day = `on ${d.toLocaleDateString(undefined, { day: "numeric", month: "short" })}`;
  return dateOnly ? day : `${day} at ${clock(d)}`;
}

// ---- small pieces ----------------------------------------------------------

const labelCls = "font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-faint";

function SectionLabel({ id, children }: { id: string; children: ReactNode }) {
  return (
    <h2 id={id} className={labelCls}>
      {children}
    </h2>
  );
}

function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div role="status" className="flex flex-col gap-2.5">
      <span className="sr-only">Loading</span>
      {Array.from({ length: lines }, (_, i) => (
        <div
          key={i}
          aria-hidden="true"
          className={`h-3 rounded bg-muted ${i % 2 ? "w-2/3" : "w-full"}`}
        />
      ))}
    </div>
  );
}

function ErrorLine({ testId }: { testId: string }) {
  return (
    <p data-testid={testId} className="text-sm text-muted-foreground">
      {ERROR_LINE}
    </p>
  );
}

function companyOf(a: { job_company?: string | null; job_title?: string | null }): string {
  return a.job_company || a.job_title || "";
}

/** Green for moving forward, amber for "they wrote back", faint otherwise. */
function stageDot(status: string): string {
  if (status === "applied" || status === "offer" || status.startsWith("interview")) return "bg-primary";
  if (status === "replied") return "bg-warning";
  return "bg-faint";
}

const STAGE_WORD: Record<string, string> = {
  considering: "Considering",
  applied: "Applied",
  replied: "Replied",
  interview_requested: "Interview",
  interview_scheduled: "Interview",
  interview_done: "Interviewed",
  offer: "Offer",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
  ghosted: "Gone quiet",
};

function StagePill({ status }: { status: string }) {
  return (
    <span
      data-testid="stage-pill"
      className="inline-flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-wide text-muted-foreground"
    >
      <span aria-hidden="true" className={`size-1.5 shrink-0 rounded-full ${stageDot(status)}`} />
      {STAGE_WORD[status] ?? status.replace(/_/g, " ")}
    </span>
  );
}

// ---- the page --------------------------------------------------------------

type Window = {
  /** The previous visit, or null = none recorded (→ "in the last 7 days"). */
  baseline: string | null;
  data: WhatsNew;
  complete: boolean;
};

export function Home() {
  // The viewer's own day: "" on the server (it has no idea of their zone),
  // the real date once in the browser — no hydration mismatch.
  const today = useSyncExternalStore(noSubscribe, todayLabel, () => "");
  const [news, setNews] = useState<Load<Window>>(LOADING);
  const [asks, setAsks] = useState<Load<{ asks: Ask[]; open_count: number }>>(LOADING);
  const [ledger, setLedger] = useState<Load<{ applications: ApplicationSummary[]; total: number }>>(
    LOADING,
  );
  const [stats, setStats] = useState<Load<Stats>>(LOADING);
  const [due, setDue] = useState<Load<ApplicationSummary[]>>(LOADING);

  const loadAsks = useCallback(async () => {
    try {
      const res = await listAsks("open");
      setAsks({ state: "ok", data: { asks: res.asks, open_count: res.open_count } });
      announce(res.open_count);
    } catch {
      // A reload after a saved answer: keep the list on screen (the save
      // worked); only a first load with nothing to show becomes the error line.
      setAsks((prev) => (prev.state === "ok" ? prev : { state: "error" }));
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    const baseline = readBaseline();
    const weekAgo = serverIso(new Date(Date.now() - WEEK_MS));
    // The window covers BOTH the count (since the last visit) and the feed
    // (the last week at least), so one walk feeds every section.
    const since = baseline && Date.parse(baseline) < Date.parse(weekAgo) ? baseline : weekAgo;

    const settle = <T,>(p: Promise<T>, set: (v: Load<T>) => void, after?: (v: T) => void) => {
      p.then(
        (data) => {
          if (cancelled) return;
          set({ state: "ok", data });
          after?.(data);
        },
        () => {
          if (!cancelled) set({ state: "error" });
        },
      );
    };

    settle(
      loadAllWhatsNew(since).then((r) => ({ baseline, ...r })),
      setNews,
      // Written only AFTER the counts above are on screen; see readBaseline.
      // Not when the walk hit its page cap: the count would be wrong, so the
      // next visit must still count from the old baseline.
      (w) => {
        if (w.complete) writeLastSeen(w.data.now);
      },
    );
    settle(
      listAsks("open").then((r) => ({ asks: r.asks, open_count: r.open_count })),
      setAsks,
    );
    settle(listApplications({ limit: LEDGER_ROWS }), setLedger);
    settle(getStats(), setStats);
    settle(
      listApplications({ due: true, limit: 5 }).then((r) => r.applications),
      setDue,
    );
    return () => {
      cancelled = true;
    };
  }, []);

  // Derived from the what's-new window.
  const derived = useMemo(() => {
    if (news.state !== "ok") return null;
    const { baseline, data, complete } = news.data;
    const since = baseline ? Date.parse(baseline) : null;
    const apps = new Map(data.applications.map((a) => [a.id, a]));
    const isAgent = (e: WhatsNewEvent) => whoLabel(e.recorded_by).who === "agent";

    const fresh = data.events.filter(
      (e) => isAgent(e) && (since === null || Date.parse(e.recorded_at) > since),
    );
    const tally = new Map<string, number>();
    for (const e of fresh) {
      const name = whoLabel(e.recorded_by).name.trim();
      if (name) tally.set(name, (tally.get(name) ?? 0) + 1);
    }
    let assistant = "Your assistant";
    let best = 0;
    for (const [name, n] of tally) {
      if (n > best) {
        best = n;
        assistant = name;
      }
    }

    // A capped walk holds the OLDEST events only, so "latest" anything from
    // it would be wrong: say nothing instead (never a guess).
    let applied: WhatsNewEvent | null = null;
    const lastByApp = new Map<number, WhatsNewEvent>();
    let feed: WhatsNewEvent[] = [];
    if (complete) {
      for (const e of data.events) {
        if (e.event_type !== "applied" || Number.isNaN(Date.parse(e.occurred_at))) continue;
        if (!applied || Date.parse(e.occurred_at) > Date.parse(applied.occurred_at)) applied = e;
      }
      // Latest event per application, for the ledger's "last thing" column.
      for (const e of data.events) lastByApp.set(e.application_id, e); // oldest → newest
      feed = data.events.filter(isAgent).slice(-FEED_ROWS).reverse();
    }

    return { baseline, complete, apps, fresh: fresh.length, assistant, applied, lastByApp, feed };
  }, [news]);

  const openCount = asks.state === "ok" ? asks.data.open_count : 0;

  return (
    <div className="grid gap-10 py-8 sm:py-10 min-[1200px]:grid-cols-[minmax(0,1fr)_332px] min-[1200px]:gap-0">
      {/* ── Reading pane ─────────────────────────────────────────────── */}
      <div className="flex min-w-0 flex-col gap-10 min-[1200px]:pr-10">
        <header className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="font-mono text-xs text-faint" data-testid="home-date">
              {today}
            </p>
            <Link
              href="/bring"
              className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground transition-opacity hover:opacity-90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
            >
              Bring a job
            </Link>
          </div>

          {news.state === "loading" && (
            <>
              <h1 className="sr-only">Home</h1>
              <Skeleton lines={2} />
            </>
          )}
          {news.state === "error" && (
            <>
              <h1 className="sr-only">Home</h1>
              <ErrorLine testId="lede-error" />
            </>
          )}
          {derived && (
            <>
              <h1
                data-testid="home-lede"
                className="max-w-3xl font-heading text-[clamp(1.875rem,1.3rem+2vw,2.75rem)] font-normal leading-[1.12] tracking-tight"
              >
                {!derived.complete ? (
                  <>Too many new records to count here — see your applications below.</>
                ) : derived.fresh > 0 ? (
                  <>
                    {derived.assistant} wrote{" "}
                    <em className="text-brand">
                      {derived.fresh} {derived.fresh === 1 ? "record" : "records"}
                    </em>{" "}
                    {derived.baseline ? "since you were last here." : "in the last 7 days."}
                  </>
                ) : (
                  <>
                    Nothing new from your assistant{" "}
                    {derived.baseline ? "since you were last here." : "in the last 7 days."}
                  </>
                )}
                {openCount > 0 && (
                  <> {openCount === 1 ? "One thing needs you." : `${openCount} things need you.`}</>
                )}
              </h1>
              {derived.applied && companyOf(apps(derived, derived.applied.application_id)) && (
                <p data-testid="home-subline" className="max-w-3xl text-[15px] text-muted-foreground">
                  The last application you confirmed was{" "}
                  {companyOf(apps(derived, derived.applied.application_id))}
                  {dayAt(derived.applied.occurred_at) ? `, ${dayAt(derived.applied.occurred_at)}` : ""}.
                </p>
              )}
            </>
          )}
        </header>

        {/* Needs you — silent when nothing is open. */}
        {asks.state === "loading" && <Skeleton lines={2} />}
        {asks.state === "error" && (
          <section aria-labelledby="home-needs-you" className="flex flex-col gap-3">
            <SectionLabel id="home-needs-you">Needs you</SectionLabel>
            <ErrorLine testId="asks-error" />
          </section>
        )}
        {asks.state === "ok" && asks.data.asks.length > 0 && (
          <section aria-labelledby="home-needs-you" className="flex flex-col gap-3" data-testid="home-needs-you">
            <div className="flex items-baseline justify-between gap-3">
              <SectionLabel id="home-needs-you">Needs you</SectionLabel>
              <Link
                href="/needs-you"
                className="text-sm font-medium text-brand underline-offset-2 hover:underline"
              >
                See all ({asks.data.open_count})
              </Link>
            </div>
            <ul className="flex flex-col gap-3">
              {asks.data.asks.slice(0, HOME_ASKS).map((a) => (
                <AskCard key={a.id} ask={a} mode="open" variant="home" onChanged={loadAsks} />
              ))}
            </ul>
          </section>
        )}

        {/* Applications ledger */}
        <section aria-labelledby="home-applications" className="flex flex-col gap-2">
          <div className="flex items-baseline justify-between gap-3">
            <SectionLabel id="home-applications">Applications</SectionLabel>
            {ledger.state === "ok" && ledger.data.total > 0 && (
              <span className="font-mono text-xs text-faint" data-testid="ledger-count">
                {ledger.data.applications.length} of {ledger.data.total}
              </span>
            )}
          </div>
          {ledger.state === "loading" && <Skeleton lines={4} />}
          {ledger.state === "error" && <ErrorLine testId="ledger-error" />}
          {ledger.state === "ok" && ledger.data.applications.length === 0 && (
            <p className="text-sm text-muted-foreground">
              Nothing yet. Bring a job from your assistant, or the{" "}
              <Link href="/bring" className="text-brand underline">
                Bring a job
              </Link>{" "}
              page, to start your record.
            </p>
          )}
          {ledger.state === "ok" && ledger.data.applications.length > 0 && (
            <>
              <ul className="flex flex-col divide-y divide-border border-y border-border">
                {ledger.data.applications.map((app) => {
                  const last = derived?.lastByApp.get(app.id);
                  const lastText = last
                    ? describeEvent(last)
                    : app.last_receipt_at
                      ? "Application sent."
                      : "";
                  const sub = [app.job_title, app.job_location].filter(Boolean).join(" · ");
                  return (
                    <li key={app.id}>
                      <Link
                        href={`/applications/${app.id}`}
                        data-testid={`ledger-row-${app.id}`}
                        className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1.5 py-3.5 hover:bg-muted/40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring sm:grid-cols-[minmax(0,1.2fr)_8.5rem_minmax(0,1.4fr)_4.5rem]"
                      >
                        <span className="flex min-w-0 flex-col">
                          <span className="truncate font-medium">
                            {app.job_company || app.job_title || "Untitled job"}
                          </span>
                          {sub && <span className="truncate text-sm text-muted-foreground">{sub}</span>}
                        </span>
                        <span className="text-right font-mono text-xs tabular-nums text-muted-foreground sm:order-last">
                          {whenLabel(app.last_event_at)}
                        </span>
                        <span className="col-span-2 sm:col-span-1">
                          <StagePill status={app.status} />
                        </span>
                        <span className="hidden truncate text-sm text-muted-foreground sm:block">
                          {lastText}
                        </span>
                      </Link>
                    </li>
                  );
                })}
              </ul>
              <div>
                <Link
                  href="/applications"
                  className="text-sm font-medium text-brand underline-offset-2 hover:underline"
                >
                  All applications
                </Link>
              </div>
            </>
          )}
        </section>
      </div>

      {/* ── Right rail (drops below the pane under ~1200px) ─────────── */}
      <aside
        aria-label="Your hunt at a glance"
        className="flex flex-col gap-9 border-t border-border pt-8 min-[1200px]:border-l min-[1200px]:border-t-0 min-[1200px]:pl-8 min-[1200px]:pt-0"
      >
        <section aria-labelledby="home-counts" className="flex flex-col gap-4">
          <SectionLabel id="home-counts">Your hunt, in counts</SectionLabel>
          {stats.state === "loading" && <Skeleton lines={2} />}
          {stats.state === "error" && <ErrorLine testId="stats-error" />}
          {stats.state === "ok" && (
            <dl className="grid grid-cols-2 gap-x-6 gap-y-5" data-testid="home-counts">
              {(
                [
                  ["Brought", stats.data.overall.brought],
                  ["Applied", stats.data.overall.applied],
                  ["Replies", stats.data.overall.replied],
                  ["Interviews", stats.data.overall.interview],
                ] as const
              ).map(([label, n]) => (
                <div key={label} className="flex flex-col-reverse gap-1">
                  <dt className="text-sm text-muted-foreground">{label}</dt>
                  <dd className="font-heading text-[34px] leading-none tabular-nums">{n}</dd>
                </div>
              ))}
            </dl>
          )}
        </section>

        {due.state === "error" && (
          <section aria-labelledby="home-due" className="flex flex-col gap-3">
            <SectionLabel id="home-due">Due</SectionLabel>
            <ErrorLine testId="due-error" />
          </section>
        )}
        {due.state === "ok" && due.data.length > 0 && (
          <section aria-labelledby="home-due" className="flex flex-col gap-2" data-testid="home-due">
            <SectionLabel id="home-due">Due</SectionLabel>
            <ul className="flex flex-col divide-y divide-border">
              {due.data.map((app) => (
                <li key={app.id}>
                  <Link
                    href={`/applications/${app.id}`}
                    className="flex items-baseline justify-between gap-3 py-2.5 text-sm hover:text-brand focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                  >
                    <span className="min-w-0 truncate">
                      Follow up with {companyOf(app) || "this job"}
                    </span>
                    {app.follow_up_on && (
                      <span className="shrink-0 font-mono text-xs text-muted-foreground">
                        {formatDayMonth(app.follow_up_on)}
                      </span>
                    )}
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        )}

        {news.state === "error" && (
          <section aria-labelledby="home-feed" className="flex flex-col gap-3">
            <SectionLabel id="home-feed">What your assistant did</SectionLabel>
            <ErrorLine testId="feed-error" />
          </section>
        )}
        {/* A capped window would show old rows as "latest" — stay silent instead. */}
        {derived && derived.complete && derived.feed.length > 0 && (
          <section aria-labelledby="home-feed" className="flex flex-col gap-3" data-testid="home-feed">
            <SectionLabel id="home-feed">What your assistant did</SectionLabel>
            <ol className="flex flex-col gap-4">
              {derived.feed.map((e) => {
                const company = companyOf(apps(derived, e.application_id));
                return (
                  <li key={e.id} className="flex flex-col gap-1">
                    <p className="font-mono text-[11px] text-faint">
                      {whenLabel(e.recorded_at)}
                      {company && ` · ${company}`}
                    </p>
                    <p className="line-clamp-2 text-sm">{describeEvent(e)}</p>
                  </li>
                );
              })}
            </ol>
          </section>
        )}
      </aside>
    </div>
  );
}

function apps(
  d: { apps: Map<number, WhatsNew["applications"][number]> },
  id: number,
): { job_company?: string | null; job_title?: string | null } {
  return d.apps.get(id) ?? {};
}
