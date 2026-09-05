"use client";

import type { ApplicationFit } from "@/lib/api";

/**
 * The fit verdict — STORED, never computed by Job360 (VISION rule 4). This
 * panel only ever displays what an agent already saved via `save_fit`; it
 * makes no scoring call of its own.
 */
export function FitPanel({ fit }: { fit: ApplicationFit | null }) {
  if (!fit) {
    return (
      <p className="text-sm text-muted-foreground">
        No fit judgement recorded yet — your agent can save one with `save_fit`.
      </p>
    );
  }

  return (
    <div className="glass-card rounded-xl p-4">
      <div className="flex items-center justify-between gap-3">
        <p className="font-semibold">{fit.verdict ?? "No verdict text"}</p>
        {fit.score != null && (
          <span className="rounded-full bg-primary/10 px-3 py-1 text-sm font-semibold text-primary">
            {fit.score}/100
          </span>
        )}
      </div>
      {fit.reasoning && <p className="mt-2 text-sm text-muted-foreground">{fit.reasoning}</p>}
      {fit.gaps.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-1.5">
          {fit.gaps.map((gap) => (
            <li
              key={gap}
              className="rounded-full bg-destructive/10 px-2.5 py-0.5 text-xs text-destructive"
            >
              {gap}
            </li>
          ))}
        </ul>
      )}
      <p className="mt-2 text-xs text-muted-foreground/70">
        recorded by {fit.recorded_by} · {new Date(fit.recorded_at).toLocaleString()}
      </p>
    </div>
  );
}
