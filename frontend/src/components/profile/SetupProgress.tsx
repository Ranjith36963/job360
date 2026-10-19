"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { fetchSettingsShared } from "@/lib/assistant-state";
import { RESUME_LINE, ROUNDS, doneDate, roundLabel } from "@/lib/assistant-tab";
import type { AssistantSettingsView } from "@/lib/api";
import { cn } from "@/lib/utils";

type Progress = AssistantSettingsView["setup_progress"];

const LABEL = "font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint";

/** The six setup rounds with a tick and the date each was done. "Next: <round>.
 *  Ask your assistant: run 360" until all six are done. */
export function SetupProgress({ progress, compact = false }: { progress: Progress; compact?: boolean }) {
  const total = progress.total || ROUNDS.length;
  const finished = progress.done >= total;
  const dates = ROUNDS.map((r) => Date.parse(progress.rounds[r.id]?.done_at ?? "")).filter((n) => !Number.isNaN(n));
  const last = dates.length ? doneDate(new Date(Math.max(...dates)).toISOString()) : "";
  return (
    <section
      data-testid={compact ? "setup-card" : "setup-progress"}
      aria-label="Setup"
      className={cn("flex flex-col gap-2.5", compact && "rounded-xl border border-border bg-card p-4 shadow-card")}
    >
      <h2 className={LABEL}>
        {finished ? `Setup · All six done${last ? `, ${last}` : ""}` : `Setup · ${progress.done} of ${total} done`}
      </h2>
      <ul className="flex flex-col">
        {ROUNDS.map((r) => {
          const at = progress.rounds[r.id]?.done_at;
          const done = at !== undefined;
          return (
            <li
              key={r.id}
              data-testid={`setup-round-${r.id}`}
              data-done={done}
              className={cn("flex items-baseline gap-2.5 border-t border-border text-sm first:border-t-0", compact ? "py-1.5" : "py-2.5")}
            >
              <span
                aria-hidden="true"
                className={cn("inline-block h-2 w-2 flex-none translate-y-px rounded-full", done ? "bg-primary" : "border border-faint")}
              />
              <span className={done ? "" : "text-muted-foreground"}>{r.label}</span>
              {done && !compact && doneDate(at) && <span className="ml-auto font-mono text-xs text-faint">{doneDate(at)}</span>}
            </li>
          );
        })}
      </ul>
      {!finished && (
        <p data-testid="setup-next" className="text-[13px] text-muted-foreground">
          {progress.next ? `Next: ${roundLabel(progress.next)}. ` : ""}
          {RESUME_LINE}
        </p>
      )}
    </section>
  );
}

/** The stored setup progress; `failed` when the read did not work (nothing is
 *  shown that was not read). */
function useSetupProgress(): { progress: Progress | null; failed: boolean } {
  const pathname = usePathname();
  const [progress, setProgress] = useState<Progress | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let cancelled = false;
    const fail = () => !cancelled && setFailed(true);
    try {
      fetchSettingsShared(pathname ?? "").then((v) => {
        if (cancelled) return;
        if (v?.setup_progress) setProgress(v.setup_progress);
        else setFailed(true);
      }, fail);
    } catch {
      fail();
    }
    return () => {
      cancelled = true;
    };
  }, [pathname]);
  return { progress, failed };
}

/** Home: the compact card, only until all six rounds are done. */
export function SetupCard() {
  const { progress } = useSetupProgress();
  if (!progress || progress.done >= (progress.total || ROUNDS.length)) return null;
  return <SetupProgress progress={progress} compact />;
}

/** Profile -> Setup: always there. */
export function SetupTab() {
  const { progress, failed } = useSetupProgress();
  if (!progress && failed) {
    return (
      <p role="alert" data-testid="setup-failed" className="pt-6 text-sm text-destructive">
        Could not load your setup progress. Reload the page to try again.
      </p>
    );
  }
  if (!progress) return <p className="pt-6 text-sm text-muted-foreground">Loading…</p>;
  return (
    <div className="max-w-xl pt-6">
      <SetupProgress progress={progress} />
    </div>
  );
}
