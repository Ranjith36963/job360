"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { ASKS_CHANGED_EVENT, getStats, listApplications, listAsks } from "@/lib/api";
import type { ApplicationSummary, Ask, StatsResponse } from "@/lib/api";
import { PageContainer } from "@/components/layout/PageContainer";
import { ApplicationRows } from "@/components/applications/ApplicationList";
import { AskCard } from "@/components/needs-you/NeedsYou";
import { CountsBlock, DueBlock, FeedBlock } from "@/components/home/HomeRail";
import { HomeWelcome } from "@/components/home/HomeWelcome";
import { fetchAllApplications, fetchWhatsNewPages } from "@/lib/home-fetch";
import {
  FEED_LIMIT,
  FEED_LOOKBACK_DAYS,
  FEED_MAX_PAGES,
  HOME_ASKS_LIMIT,
  buildSentence,
  daysAgoIso,
  formatToday,
  isBrandNew,
  readLastVisit,
  selectFeed,
  writeLastVisit,
} from "@/lib/home";
import type { FeedItem } from "@/lib/home";
import type { WhatsNewPages } from "@/lib/home-fetch";

// ---------------------------------------------------------------------------
// The signed-in Home (redesign slice 2): one sentence on what changed, the
// questions waiting for you, the whole ledger, and a right pane with counts,
// what is due and what your assistant did.
//
// Four independent calls feed it. Each can fail on its own: a failed
// right-pane block shows nothing, a failed ledger shows an error with a retry,
// a failed sentence falls back to the plain heading. Job360 judges nothing
// here — every string below is stored data rendered as a React text node.
// ---------------------------------------------------------------------------

type Load<T> = { status: "loading" } | { status: "error" } | { status: "ok"; value: T };
const LOADING = { status: "loading" } as const;

type SinceVisit = Pick<WhatsNewPages, "events" | "truncated" | "now">;
type Asks = { asks: Ask[]; openCount: number };
type Feed = { items: FeedItem[]; eventCount: number };

const LABEL = "font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint";
const linkBtn =
  "text-xs font-medium text-muted-foreground underline-offset-2 hover:text-foreground hover:underline";

const subscribeNever = () => () => {};

function announce(openCount: number) {
  window.dispatchEvent(new CustomEvent(ASKS_CHANGED_EVENT, { detail: openCount }));
}

export function Home() {
  // Client-only: the date and the last-visit stamp belong to this browser, so
  // the server render leaves the date empty instead of guessing a timezone.
  const today = useSyncExternalStore(subscribeNever, () => formatToday(), () => "");

  const [since, setSince] = useState<Load<SinceVisit>>(LOADING);
  const [asks, setAsks] = useState<Load<Asks>>(LOADING);
  const [apps, setApps] = useState<Load<{ list: ApplicationSummary[]; total: number }>>(LOADING);
  const [stats, setStats] = useState<Load<StatsResponse["overall"]>>(LOADING);
  const [due, setDue] = useState<Load<ApplicationSummary[]>>(LOADING);
  const [feed, setFeed] = useState<Load<Feed>>(LOADING);

  // Which "since" this page view uses — read once, so a retry or a reload after
  // answering a question keeps the same window instead of jumping to "now".
  const sinceRef = useRef<string | null>(null);
  const epoch = useRef(0);

  const run = useCallback(() => {
    const mine = ++epoch.current;
    const live = () => mine === epoch.current;
    if (sinceRef.current === null) sinceRef.current = readLastVisit();
    const sinceVisit = sinceRef.current;

    fetchWhatsNewPages(sinceVisit)
      .then((r) => live() && setSince({ status: "ok", value: r }))
      .catch(() => live() && setSince({ status: "error" }));

    listAsks("open")
      .then((r) => live() && setAsks({ status: "ok", value: { asks: r.asks, openCount: r.open_count } }))
      .catch(() => live() && setAsks({ status: "error" }));

    fetchAllApplications()
      .then((r) => live() && setApps({ status: "ok", value: { list: r.applications, total: r.total } }))
      .catch(() => live() && setApps({ status: "error" }));

    getStats()
      .then((r) => {
        if (!live()) return;
        setStats(r?.overall ? { status: "ok", value: r.overall } : { status: "error" });
      })
      .catch(() => live() && setStats({ status: "error" }));

    listApplications({ due: true, limit: 200 })
      .then((r) => live() && setDue({ status: "ok", value: r.applications }))
      .catch(() => live() && setDue({ status: "error" }));

    fetchWhatsNewPages(daysAgoIso(FEED_LOOKBACK_DAYS), FEED_MAX_PAGES)
      .then(
        (r) =>
          live() &&
          setFeed({
            status: "ok",
            value: { items: selectFeed(r.events, r.applications, FEED_LIMIT), eventCount: r.events.length },
          })
      )
      .catch(() => live() && setFeed({ status: "error" }));
  }, []);

  useEffect(() => {
    const token = epoch;
    run();
    return () => {
      token.current++;
    };
  }, [run]);

  // Store "now" as the new last visit only AFTER the sentence has rendered, so
  // a reload shows the quiet state but this view still shows what was new.
  // The sentence also waits for the asks call, so the stamp does too — a visit
  // that only ever showed the skeleton must not count as seen.
  const sinceNow = since.status === "ok" ? since.value.now : null;
  const asksSettled = asks.status !== "loading";
  useEffect(() => {
    if (sinceNow && asksSettled) writeLastVisit(sinceNow);
  }, [sinceNow, asksSettled]);

  function retry() {
    setSince(LOADING);
    setAsks(LOADING);
    setApps(LOADING);
    setStats(LOADING);
    setDue(LOADING);
    setFeed(LOADING);
    run();
  }

  // After an answer or a withdraw: re-read the open questions and tell the
  // sidebar badge, exactly as the Needs-you page does.
  const reloadAsks = useCallback(async () => {
    try {
      const r = await listAsks("open");
      setAsks({ status: "ok", value: { asks: r.asks, openCount: r.open_count } });
      announce(r.open_count);
    } catch {
      // Keep what is on screen; the next full load will correct it.
    }
  }, []);

  const brandNew = isBrandNew({
    applicationsTotal: apps.status === "ok" ? apps.value.total : null,
    eventCount: feed.status === "ok" ? feed.value.eventCount : null,
    openAsks: asks.status === "ok" ? asks.value.openCount : null,
  });

  const settled =
    since.status !== "loading" && asks.status !== "loading" && apps.status !== "loading" && feed.status !== "loading";

  if (brandNew) {
    return (
      <PageContainer className="flex flex-col gap-8 py-8 sm:py-12">
        <h1 className="font-heading text-4xl font-normal tracking-tight">Home</h1>
        <HomeWelcome />
      </PageContainer>
    );
  }

  const openCount = asks.status === "ok" ? asks.value.openCount : 0;
  const sentenceReady = since.status !== "loading" && asks.status !== "loading";
  const parts =
    since.status === "ok"
      ? buildSentence({ events: since.value.events, truncated: since.value.truncated, openAsks: openCount })
      : null;

  const dueList = due.status === "ok" ? due.value : [];
  const feedItems = feed.status === "ok" ? feed.value.items : [];
  const showRail =
    !settled ||
    stats.status === "loading" ||
    due.status === "loading" ||
    stats.status === "ok" ||
    dueList.length > 0 ||
    feedItems.length > 0;

  return (
    <PageContainer className="py-8 sm:py-12">
      <div
        className={`grid gap-10 ${showRail ? "min-[1000px]:grid-cols-[minmax(0,1fr)_332px] min-[1000px]:gap-0" : ""}`}
      >
        <div className="flex min-w-0 flex-col gap-10 min-[1000px]:pr-10">
          <header className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
            <div className="min-w-0 flex-1 basis-80">
              <p className="font-mono text-xs text-faint" data-testid="home-date">
                {today || " "}
              </p>
              <h1
                data-testid="home-sentence"
                className="mt-2 text-balance font-heading text-[clamp(1.9rem,3.6vw,2.75rem)] font-normal leading-[1.08] tracking-[-0.022em]"
              >
                {!sentenceReady ? (
                  <>
                    <span className="sr-only">Home</span>
                    <span
                      aria-hidden="true"
                      data-testid="home-sentence-skeleton"
                      className="block h-10 w-3/4 animate-pulse rounded-md bg-muted"
                    />
                  </>
                ) : parts ? (
                  parts.map((p, i) =>
                    p.em ? (
                      <em key={i} className="not-italic text-brand">
                        {p.text}
                      </em>
                    ) : (
                      <span key={i}>{p.text}</span>
                    )
                  )
                ) : (
                  "Home"
                )}
              </h1>
            </div>
            <Link
              href="/bring"
              className="shrink-0 rounded-lg border border-border bg-card px-3 py-1.5 text-sm font-medium transition-colors hover:bg-muted"
            >
              Jobs in
            </Link>
          </header>

          {asks.status === "ok" && asks.value.asks.length > 0 && (
            <section aria-labelledby="home-asks" className="flex flex-col gap-3" data-testid="home-asks">
              <div className="flex items-baseline justify-between gap-3">
                <h2 id="home-asks" className={LABEL}>
                  Needs you
                </h2>
                {(asks.value.asks.length > HOME_ASKS_LIMIT || asks.value.openCount > HOME_ASKS_LIMIT) && (
                  <Link href="/needs-you" className={linkBtn}>
                    See all
                  </Link>
                )}
              </div>
              <ul className="flex flex-col gap-3">
                {asks.value.asks.slice(0, HOME_ASKS_LIMIT).map((a) => (
                  <AskCard key={a.id} ask={a} mode="open" onChanged={reloadAsks} />
                ))}
              </ul>
            </section>
          )}

          {apps.status === "error" && (
            <div role="alert" className="flex items-center gap-3 text-sm text-destructive">
              <span>Could not load your applications.</span>
              <button type="button" data-testid="home-retry" onClick={retry} className={linkBtn}>
                Try again
              </button>
            </div>
          )}
          {apps.status === "loading" && (
            <p className="text-sm text-muted-foreground">Loading your applications…</p>
          )}
          {apps.status === "ok" && apps.value.list.length > 0 && (
            <section aria-labelledby="home-apps" data-testid="home-apps">
              <div className="mb-1 flex items-baseline justify-between gap-3">
                <h2 id="home-apps" className={LABEL}>
                  Applications
                </h2>
                <span className="font-mono text-xs text-faint">{apps.value.total}</span>
              </div>
              <ApplicationRows applications={apps.value.list} />
            </section>
          )}
        </div>

        {showRail && (
          <aside
            data-testid="home-rail"
            className="flex min-w-0 flex-col gap-7 border-t border-border pt-8 min-[1000px]:border-l min-[1000px]:border-t-0 min-[1000px]:pl-8 min-[1000px]:pt-0"
          >
            {stats.status === "ok" && <CountsBlock overall={stats.value} />}
            <DueBlock due={dueList} />
            <FeedBlock items={feedItems} />
          </aside>
        )}
      </div>
    </PageContainer>
  );
}
