"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { getNeedsYouCount, listAsks } from "@/lib/api";
import type { ApplicationSummary, Ask } from "@/lib/api";
import { PageContainer } from "@/components/layout/PageContainer";
import { MorningCheck } from "@/components/needs-you/MorningCheck";
import { BatchApplyPanel } from "@/components/needs-you/BatchApplyPanel";
import { FeedBlock } from "@/components/home/HomeRail";
import { HomeWelcome } from "@/components/home/HomeWelcome";
import { fetchAllApplications, fetchWhatsNewPages } from "@/lib/home-fetch";
import {
  FEED_LIMIT,
  FEED_LOOKBACK_DAYS,
  FEED_MAX_PAGES,
  buildSentence,
  dayAt,
  daysAgoIso,
  formatToday,
  isBrandNew,
  lastApplied,
  readLastVisit,
  selectFeed,
  writeLastVisit,
} from "@/lib/home";
import type { FeedItem, Waits } from "@/lib/home";
import type { WhatsNewPages } from "@/lib/home-fetch";

// ---------------------------------------------------------------------------
// The signed-in Progress dashboard (owner, 2026-10-28): NOW and RECENT only.
// One sentence on what changed, "Right now" (the morning check), "Next up" (the
// batch queue) and "Activity" (what your assistant did), in one column. The
// counts and the lessons live on Statistics (/stats).
//
// Independent calls feed it. Each can fail on its own: a failed
// right-pane block shows nothing, a failed ledger shows an error with a retry,
// a failed sentence falls back to the plain heading. Job360 judges nothing
// here — every string below is stored data rendered as a React text node.
// ---------------------------------------------------------------------------

type Load<T> = { status: "loading" } | { status: "error" } | { status: "ok"; value: T };
const LOADING = { status: "loading" } as const;

type SinceVisit = Pick<WhatsNewPages, "events" | "truncated" | "now">;
type Asks = { asks: Ask[]; openCount: number };
type Feed = { items: FeedItem[]; eventCount: number; applied: ReturnType<typeof lastApplied> };

const linkBtn =
  "text-xs font-medium text-muted-foreground underline-offset-2 hover:text-foreground hover:underline";

const GROUP = "font-heading text-xl font-normal";

const subscribeNever = () => () => {};

export function Home() {
  // Client-only: the date and the last-visit stamp belong to this browser, so
  // the server render leaves the date empty instead of guessing a timezone.
  const today = useSyncExternalStore(subscribeNever, () => formatToday(), () => "");

  const [since, setSince] = useState<Load<SinceVisit>>(LOADING);
  const [asks, setAsks] = useState<Load<Asks>>(LOADING);
  const [waits, setWaits] = useState<Load<Waits>>(LOADING);
  const [apps, setApps] = useState<Load<{ list: ApplicationSummary[]; total: number }>>(LOADING);
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

    listAsks("open")
      .then((r) => live() && setAsks({ status: "ok", value: { asks: r.asks, openCount: r.open_count } }))
      .catch(() => live() && setAsks({ status: "error" }));

    getNeedsYouCount()
      .then((c) => live() && setWaits({ status: "ok", value: { you: c.by_actor.you ?? 0, your_pc: c.by_actor.your_pc ?? 0, assistant: c.by_actor.assistant ?? 0 } }))
      .catch(() => live() && setWaits({ status: "error" }));

    fetchAllApplications()
      .then((r) => live() && setApps({ status: "ok", value: { list: r.applications, total: r.total } }))
      .catch(() => live() && setApps({ status: "error" }));

    // ONE whats-new read feeds both the sentence and the feed: the last-visit
    // window is cut out of the 30-day read here instead of asking twice. (A
    // last visit older than the window is counted from the window's start.)
    const sinceMs = Date.parse(sinceVisit);
    fetchWhatsNewPages(daysAgoIso(FEED_LOOKBACK_DAYS), FEED_MAX_PAGES)
      .then((r) => {
        if (!live()) return;
        setFeed({
          status: "ok",
          value: {
            items: selectFeed(r.events, r.applications, FEED_LIMIT),
            eventCount: r.events.length,
            applied: lastApplied(r.events, r.applications),
          },
        });
        setSince({
          status: "ok",
          value: {
            events: r.events.filter((e) => !(Date.parse(e.recorded_at) <= sinceMs)),
            truncated: r.truncated,
            now: r.now,
          },
        });
      })
      .catch(() => {
        if (!live()) return;
        setFeed({ status: "error" });
        setSince({ status: "error" });
      });
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
    setWaits(LOADING);
    setApps(LOADING);
    setFeed(LOADING);
    run();
  }


  const brandNew = isBrandNew({
    applicationsTotal: apps.status === "ok" ? apps.value.total : null,
    eventCount: feed.status === "ok" ? feed.value.eventCount : null,
    openAsks: asks.status === "ok" ? asks.value.openCount : null,
  });

  if (brandNew) {
    return (
      <PageContainer className="flex flex-col gap-8 py-8 sm:py-12">
        <h1 className="font-heading text-4xl font-normal tracking-tight">Progress dashboard</h1>
        <HomeWelcome />
      </PageContainer>
    );
  }

  const sentenceReady = since.status !== "loading" && waits.status !== "loading";
  const parts =
    since.status === "ok"
      ? buildSentence({ events: since.value.events, truncated: since.value.truncated, waits: waits.status === "ok" ? waits.value : null })
      : null;

  const feedItems = feed.status === "ok" ? feed.value.items : [];
  const applied = feed.status === "ok" ? feed.value.applied : null;
  const appliedWhen = applied ? dayAt(applied.occurredAt) : "";

  return (
    <PageContainer className="py-8 sm:py-12">
      <div className="flex min-w-0 flex-col gap-10">
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
                  <span className="sr-only">Progress dashboard</span>
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
                "Progress dashboard"
              )}
            </h1>
            {applied && (
              <p data-testid="home-subline" className="mt-3 text-[15px] text-muted-foreground">
                The last application you confirmed was {applied.company}
                {appliedWhen ? `, ${appliedWhen}` : ""}.
              </p>
            )}
          </div>
          <Link
            href="/bring"
            className="shrink-0 rounded-lg border border-border bg-card px-3 py-1.5 text-sm font-medium transition-colors hover:bg-muted"
          >
            Jobs in
          </Link>
        </header>

        <section aria-labelledby="home-now" data-testid="home-now" className="flex flex-col gap-4">
          <h2 id="home-now" className={GROUP}>
            Right now
          </h2>
          <MorningCheck />
        </section>

        {apps.status === "error" && (
          <div role="alert" className="flex items-center gap-3 text-sm text-destructive">
            <span>Could not load your applications.</span>
            <button type="button" data-testid="home-retry" onClick={retry} className={linkBtn}>
              Try again
            </button>
          </div>
        )}

        <section aria-labelledby="home-next" data-testid="home-next" className="flex flex-col gap-4">
          <h2 id="home-next" className={GROUP}>
            Next up
          </h2>
          <BatchApplyPanel />
        </section>

        <FeedBlock items={feedItems} />

        <Link href="/stats" data-testid="home-stats-link" className={`${linkBtn} self-start text-sm`}>
          See your statistics &rarr;
        </Link>
      </div>
    </PageContainer>
  );
}
