"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import type { ApplicationSummary } from "@/lib/api";
import { fetchAllApplications } from "@/lib/home-fetch";
import { formatDayMonth } from "@/lib/format-date";

const LABEL = "font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint";

/** Follow-ups that are due, soonest first. Each ledger row carries `follow_up_due`, so this
 *  needs no second request. */
export function dueFollowUps(apps: readonly ApplicationSummary[]): ApplicationSummary[] {
  return apps
    .filter((a) => a.follow_up_due)
    .sort((a, b) => (a.follow_up_on ?? "").localeCompare(b.follow_up_on ?? ""));
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
            <Link href={`/applications/${a.id}`} className="min-w-0 underline-offset-2 hover:underline">
              Follow up with {a.job_company || a.job_title}
            </Link>
            {a.follow_up_on && (
              <span className="shrink-0 font-mono text-xs text-faint">{formatDayMonth(a.follow_up_on)}</span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

/** "Due" on Needs you (moved from Home, owner 2026-10-28). Silent when nothing is due or the
 *  read fails. */
export function DueFollowUps() {
  const [due, setDue] = useState<ApplicationSummary[]>([]);
  useEffect(() => {
    let cancelled = false;
    try {
      fetchAllApplications().then(
        (r) => {
          if (!cancelled) setDue(dueFollowUps(r.applications));
        },
        () => undefined,
      );
    } catch {
      // best effort: nothing due is shown
    }
    return () => {
      cancelled = true;
    };
  }, []);
  return <DueBlock due={due} />;
}
