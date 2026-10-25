"use client";

import { useState } from "react";
import type { Ask } from "@/lib/api";
import { Textarea } from "@/components/ui/textarea";
import { AnswerText } from "@/components/AnswerText";
import { initialRow, isSensitiveQuestion } from "@/lib/job-cards";
import type { MergedRow } from "@/lib/job-cards";

// Question text, answers and choices are written by the user's assistant: untrusted, rendered as
// React text only.

export type AskQuestion = NonNullable<Ask["questions"]>[number];
/** One row's state: the answer as it stands and whether the user has checked it. */
export type RowState = { value: string; ok: boolean };

const btn = "rounded-md border border-border px-2.5 py-1 text-xs font-medium hover:bg-muted/50 disabled:opacity-50";
const picked = "rounded-md bg-primary px-2.5 py-1 text-xs font-semibold text-primary-foreground";
const good = "bg-success-soft text-success";
const warn = "bg-amber-100 text-amber-950 dark:bg-amber-400/20 dark:text-amber-100";

export function AskQuestions({
  items,
  rows,
  busy,
  onChange,
}: {
  items: MergedRow[];
  rows: Record<string, RowState>;
  busy: boolean;
  onChange: (rowId: string, row: RowState) => void;
}) {
  const [editing, setEditing] = useState<Set<string>>(new Set());
  const edit = (id: string) => setEditing((prev) => new Set(prev).add(id));

  return (
    <>
      {items.map(({ id, q, askId }) => {
        const row = rows[id] ?? initialRow(q);
        const open = editing.has(id) || (!q.choices?.length && !q.answer);
        const state = row.ok ? "Verified" : "Guessed";
        const sensitive = isSensitiveQuestion(q);
        return (
          <div key={id} className="flex flex-col gap-2 rounded-lg bg-muted/30 p-3" data-testid={`q-${askId}-${q.key}`}>
            <div className="flex flex-wrap items-start justify-between gap-2">
              <p className="whitespace-pre-wrap text-sm font-medium">{q.question}</p>
              <span className="flex flex-wrap gap-1">
                {sensitive && !row.ok && <span className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${warn}`}>Needs your click</span>}
                <span data-testid={`q-state-${askId}-${q.key}`}
                  className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${state === "Verified" ? good : warn}`}>
                  {state}
                </span>
              </span>
            </div>
            {q.choices?.length ? (
              <div className="flex flex-wrap gap-2">
                {q.choices.map((c) => (
                  <button key={c} type="button" disabled={busy} aria-pressed={row.ok && row.value === c}
                    className={row.ok && row.value === c ? picked : btn} onClick={() => onChange(id, { value: c, ok: true })}>
                    {c}
                  </button>
                ))}
              </div>
            ) : open ? (
              <Textarea aria-label={q.question} value={row.value} disabled={busy} placeholder="Type your answer" data-testid={`q-input-${askId}-${q.key}`}
                onChange={(e) => onChange(id, { value: e.target.value, ok: e.target.value.trim().length > 0 })} />
            ) : (
              <>
                <p className="whitespace-pre-wrap text-sm"><AnswerText value={row.value} /></p>
                <div className="flex gap-2">
                  <button type="button" className={btn} disabled={busy} data-testid={`q-edit-${askId}-${q.key}`} onClick={() => edit(id)}>
                    Edit
                  </button>
                  <button type="button" disabled={busy || row.ok} data-testid={`q-ok-${askId}-${q.key}`}
                    className={row.ok ? picked : btn} onClick={() => onChange(id, { value: row.value, ok: true })}>
                    {row.ok ? "Checked" : "Looks right"}
                  </button>
                </div>
              </>
            )}
          </div>
        );
      })}
    </>
  );
}
