"use client";

import Link from "next/link";
import type { ApplicationSummary, StatsResponse } from "@/lib/api";
import { formatDayMonth } from "@/lib/format-date";
import { formatFeedTime } from "@/lib/home";
import type { FeedItem } from "@/lib/home";

// The right pane. Every block hides itself when it has nothing to say (owner
// rule #29: empty stays silent) and a block whose call failed is simply not
// passed in — it never breaks the page. Every string here came from the API,
// so it is only ever rendered as a React text node.

const LABEL = "font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint";

export function CountsBlock({ overall }: { overall: StatsResponse["overall"] }) {
  const items: [number, string][] = [
    [overall.brought, "jobs brought"],
    [overall.applied, "applied"],
    [overall.replied, "replies"],
    [overall.interview, "interviews"],
  ];
  return (
    <section aria-labelledby="home-counts" data-testid="home-counts">
      <h2 id="home-counts" className={`${LABEL} mb-3.5`}>
        Your hunt, in counts
      </h2>
      <ul className="grid grid-cols-2 gap-x-3 gap-y-4">
        {items.map(([n, label]) => (
          <li key={label}>
            <span className="block font-heading text-4xl leading-none">{n}</span>
            <span className="mt-1 block text-[12.5px] text-muted-foreground">{label}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function DueBlock({ due }: { due: ApplicationSummary[] }) {
  if (due.length === 0) return null;
  return (
    <section aria-labelledby="home-due" data-testid="home-due">
      <h2 id="home-due" className={`${LABEL} mb-1.5`}>
        Due
      </h2>
      <ul>
        {due.map((a) => (
          <li
            key={a.id}
            className="flex items-baseline justify-between gap-3 border-t border-border py-2 text-sm first:border-t-0"
          >
            <Link
              href={`/applications/${a.id}`}
              className="min-w-0 underline-offset-2 hover:underline"
            >
              Follow up with {a.job_company || a.job_title}
            </Link>
            {a.follow_up_on && (
              <span className="shrink-0 font-mono text-xs text-faint">
                {formatDayMonth(a.follow_up_on)}
              </span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

export function FeedBlock({ items }: { items: FeedItem[] }) {
  if (items.length === 0) return null;
  return (
    <section aria-labelledby="home-feed" data-testid="home-feed">
      <h2 id="home-feed" className={`${LABEL} mb-3`}>
        What your assistant did
      </h2>
      <ul>
        {items.map((it) => (
          <li
            key={it.id}
            className="border-t border-border py-2.5 text-[13px] first:border-t-0 first:pt-0"
          >
            <div className="mb-0.5 flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="font-mono text-[11.5px] text-faint">
                {formatFeedTime(it.recordedAt)}
                {it.company && " · "}
                {it.company && (
                  <Link
                    href={`/applications/${it.applicationId}`}
                    className="underline-offset-2 hover:underline"
                  >
                    {it.company}
                  </Link>
                )}
              </span>
              <span className="rounded-full bg-muted px-2 py-px font-mono text-[10.5px] text-muted-foreground">
                {it.assistant}
              </span>
            </div>
            <p className="line-clamp-2 break-words">{it.line}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}
