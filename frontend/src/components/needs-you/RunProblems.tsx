"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { getRunProblems, type RunProblem } from "@/lib/api";
import { StartLinkProblems } from "./StartLinkProblems";

/**
 * Owner 2026-10-25: any problem with Hunt, Apply, Gmail check or People hunt shows as a card on Home (Job360-wide
 * problems on Home; a single job's problems on Needs you). One card per duty that failed 3 times in a row (in
 * plain words, with what to do), and the start-link problems below it. Silent when everything works or the list
 * cannot load.
 */
export function RunProblems() {
  const [problems, setProblems] = useState<RunProblem[]>([]);

  useEffect(() => {
    let live = true;
    Promise.resolve()
      .then(() => getRunProblems())
      .then((out) => {
        if (live) setProblems(out.problems);
      })
      .catch(() => undefined); // a missing list is never an error on Home
    return () => {
      live = false;
    };
  }, []);

  return (
    <>
      {problems.length > 0 && (
        <section className="flex flex-col gap-2" aria-label="Problems with your assistant's runs" data-testid="run-problems">
          {problems.map((p) => (
            <div
              key={p.duty}
              role="status"
              className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm"
              data-testid={`run-problem-${p.duty}`}
            >
              <p className="font-medium">{p.title}</p>
              <p className="mt-1 text-muted-foreground">{p.what_to_do}</p>
              <p className="mt-1 text-xs text-muted-foreground">
                It now waits longer between tries.{" "}
                <Link href="/settings/connect?tab=schedules" className="underline underline-offset-2">
                  Run it now
                </Link>
              </p>
            </div>
          ))}
        </section>
      )}
      <StartLinkProblems />
    </>
  );
}
