"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { getDutyTriggers, type DutyTrigger } from "@/lib/api";

/** A last start that did not reach the user's assistant (closed results written by duty_trigger.py). */
export function startFailed(row: DutyTrigger): boolean {
  if (!row.set) return false;
  const result = row.last_result ?? "";
  return result.startsWith("failed") || result.startsWith("refused") || result === "rate_limited";
}

/**
 * Ignition (owner 2026-10-24): a start link that failed to start the user's assistant shows here, so a
 * press that did nothing is never silent. The hourly run still does the work; this says why it was not at once.
 */
export function StartLinkProblems() {
  const [failed, setFailed] = useState<DutyTrigger[]>([]);

  useEffect(() => {
    let live = true;
    Promise.resolve()
      .then(() => getDutyTriggers())
      .then((out) => {
        if (live) setFailed(out.duties.filter(startFailed));
      })
      .catch(() => undefined); // a missing list is never an error on Needs you
    return () => {
      live = false;
    };
  }, []);

  if (failed.length === 0) return null;
  return (
    <div role="status" className="rounded-lg border border-border bg-muted/30 p-3 text-sm" data-testid="start-link-problems">
      <p className="font-medium">Your assistant did not start at once</p>
      <ul className="mt-1 list-disc pl-5 text-muted-foreground">
        {failed.map((row) => (
          <li key={row.duty} data-testid={`start-failed-${row.duty}`}>
            {row.label}: {row.set && row.last_result === "rate_limited" ? "busy, waiting as it asked" : "the start link did not work"}.
          </li>
        ))}
      </ul>
      <p className="mt-1 text-xs text-muted-foreground">
        Your hourly run still does the work.{" "}
        <Link href="/settings/connect?tab=schedules" className="underline underline-offset-2">
          Check your start links
        </Link>
      </p>
    </div>
  );
}
