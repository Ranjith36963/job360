"use client";

import { useState } from "react";
import type { Ask } from "@/lib/api";
import { Textarea } from "@/components/ui/textarea";
import { AnswerText } from "@/components/AnswerText";

// Question text, answers and choices are written by the user's assistant: untrusted, rendered as
// React text only.

export type AskQuestion = NonNullable<Ask["questions"]>[number];
/** One row's state: the answer as it stands and whether the user has checked it. */
export type RowState = { value: string; ok: boolean };

const SOURCE_LABEL: Record<string, string> = {
  profile: "From your profile",
  memory: "From memory",
  cv: "From your CV",
  guess: "Guessed, please check",
  draft: "Drafted for you",
  none: "Not known yet",
};

export function sourceLabel(q: AskQuestion): string {
  const label = SOURCE_LABEL[q.source] ?? "From your assistant";
  return q.source === "memory" && q.remembered_on ? `${label} (${q.remembered_on})` : label;
}

/** Every row starts from the assistant's answer, unchecked. */
export function initialRows(questions: AskQuestion[]): Record<string, RowState> {
  return Object.fromEntries(questions.map((q) => [q.key, { value: q.answer, ok: false }]));
}

const btn = "rounded-md border border-border px-2.5 py-1 text-xs font-medium hover:bg-muted/50 disabled:opacity-50";
const picked = "rounded-md bg-primary px-2.5 py-1 text-xs font-semibold text-primary-foreground";
const warn = "bg-amber-100 text-amber-950 dark:bg-amber-400/20 dark:text-amber-100";

export function AskQuestions({
  askId,
  questions,
  rows,
  busy,
  onChange,
}: {
  askId: number;
  questions: AskQuestion[];
  rows: Record<string, RowState>;
  busy: boolean;
  onChange: (key: string, row: RowState) => void;
}) {
  const [editing, setEditing] = useState<Set<string>>(new Set());
  const edit = (key: string) => setEditing((prev) => new Set(prev).add(key));

  return (
    <>
      {questions.map((q) => {
        const row = rows[q.key] ?? { value: q.answer, ok: false };
        const open = editing.has(q.key) || (!q.choices?.length && !q.answer);
        const unsure = q.source === "guess" || q.source === "none";
        return (
          <div key={q.key} className="flex flex-col gap-2 rounded-lg bg-muted/30 p-3" data-testid={`q-${askId}-${q.key}`}>
            <div className="flex flex-wrap items-start justify-between gap-2">
              <p className="whitespace-pre-wrap text-sm font-medium">{q.question}</p>
              <span className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${unsure ? warn : "bg-muted text-muted-foreground"}`}>
                {sourceLabel(q)}
              </span>
            </div>
            {q.choices?.length ? (
              <div className="flex flex-wrap gap-2">
                {q.choices.map((c) => (
                  <button key={c} type="button" disabled={busy} aria-pressed={row.ok && row.value === c}
                    className={row.ok && row.value === c ? picked : btn} onClick={() => onChange(q.key, { value: c, ok: true })}>
                    {c}
                  </button>
                ))}
              </div>
            ) : open ? (
              <Textarea aria-label={q.question} value={row.value} disabled={busy} data-testid={`q-input-${askId}-${q.key}`}
                onChange={(e) => onChange(q.key, { value: e.target.value, ok: e.target.value.trim().length > 0 })} />
            ) : (
              <>
                <p className="whitespace-pre-wrap text-sm"><AnswerText value={row.value} /></p>
                <div className="flex gap-2">
                  <button type="button" className={btn} disabled={busy} data-testid={`q-edit-${askId}-${q.key}`} onClick={() => edit(q.key)}>
                    Edit
                  </button>
                  <button type="button" disabled={busy || row.ok} data-testid={`q-ok-${askId}-${q.key}`}
                    className={row.ok ? picked : btn} onClick={() => onChange(q.key, { value: row.value, ok: true })}>
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
