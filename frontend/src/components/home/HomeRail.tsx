"use client";

import Link from "next/link";
import { formatFeedTime } from "@/lib/home";
import type { FeedItem } from "@/lib/home";

// The Activity block. It hides itself when it has nothing to say (owner
// rule #29: empty stays silent) and a block whose call failed is simply not
// passed in — it never breaks the page. Every string here came from the API,
// so it is only ever rendered as a React text node.

export function FeedBlock({ items }: { items: FeedItem[] }) {
  if (items.length === 0) return null;
  return (
    <section aria-labelledby="home-feed" data-testid="home-feed">
      <h2 id="home-feed" className="mb-4 font-heading text-xl font-normal">
        Activity
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
