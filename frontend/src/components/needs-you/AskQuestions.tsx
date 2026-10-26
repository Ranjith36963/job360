"use client";

import { useState } from "react";
import type { Ask } from "@/lib/api";
import { Textarea } from "@/components/ui/textarea";
import { AnswerValue, DraftMissing } from "./AnswerValue";
import { looksLikePlaceholder } from "@/lib/answer-text";
import { initialRow, isSensitiveQuestion, isVerifiedSource } from "@/lib/job-cards";
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
  onSave,
}: {
  items: MergedRow[];
  rows: Record<string, RowState>;
  busy: boolean;
  onChange: (rowId: string, row: RowState) => void;
  /** Saves one row at once (Save after an Edit, Looks right, a choice): the owner's click is the consent. */
  onSave?: (rowId: string, value: string) => void;
}) {
  // Open editors, each with the row as it was, so Cancel puts it back.
  const [editing, setEditing] = useState<Map<string, RowState>>(new Map());
  const edit = (id: string, row: RowState) => setEditing((prev) => new Map(prev).set(id, row));
  const close = (id: string) =>
    setEditing((prev) => {
      const next = new Map(prev);
      next.delete(id);
      return next;
    });

  return (
    <>
      {items.map(({ id, q, askId }) => {
        const row = rows[id] ?? initialRow(q);
        const open = editing.has(id);
        // No answer yet and nothing to pick from: not a guess. The assistant drafts it, or the owner writes it.
        const pending = !q.choices?.length && !open && row.value.trim() === "";
        // Owner 2026-10-26: a question the assistant could not even guess reads "Not known" until you answer it.
        const notKnown = q.source === "not_known" && !open && row.value.trim() === "";
        // A saved fact (profile/memory/cv) left as it is reads Verified even while a consent or personal
        // row waits for the owner's own click ("Needs your click" still shows); a guess reads Guessed until checked.
        const saved = isVerifiedSource(q.source) && q.answer.trim() !== "" && row.value === q.answer;
        const state = row.ok || saved ? "Verified" : "Guessed";
        const sensitive = isSensitiveQuestion(q);
        const stub = !q.choices?.length && !open && looksLikePlaceholder(q.question, row.value);
        return (
          <div key={id} className="flex flex-col gap-2 rounded-lg bg-muted/30 p-3" data-testid={`q-${askId}-${q.key}`}>
            <div className="flex flex-wrap items-start justify-between gap-2">
              <p className="whitespace-pre-wrap text-sm font-medium">{q.question}</p>
              <span className="flex flex-wrap gap-1">
                {sensitive && !row.ok && <span className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${warn}`}>Needs your click</span>}
                {notKnown && (
                  <span data-testid={`q-state-${askId}-${q.key}`} className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${warn}`}>
                    Not known
                  </span>
                )}
                {!pending && !stub && !notKnown && (
                  <span data-testid={`q-state-${askId}-${q.key}`}
                    className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${state === "Verified" ? good : warn}`}>
                    {state}
                  </span>
                )}
                {row.ok && q.answer.trim() !== "" && row.value.trim() !== q.answer.trim() && (
                  <span data-testid={`q-changed-${askId}-${q.key}`} className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${good}`}>You changed</span>
                )}
              </span>
            </div>
            {q.choices?.length ? (
              <div className="flex flex-wrap gap-2">
                {q.choices.map((c) => (
                  <button key={c} type="button" disabled={busy} aria-pressed={row.ok && row.value === c}
                    className={row.ok && row.value === c ? picked : btn} onClick={() => {
                      onChange(id, { value: c, ok: true });
                      onSave?.(id, c);
                    }}>
                    {c}
                  </button>
                ))}
              </div>
            ) : open ? (
              <>
                <Textarea aria-label={q.question} value={row.value} disabled={busy} placeholder="Type your answer" data-testid={`q-input-${askId}-${q.key}`}
                  onChange={(e) => onChange(id, { value: e.target.value, ok: e.target.value.trim().length > 0 })} />
                <div className="flex gap-2">
                  <button type="button" className={picked} disabled={busy || !row.value.trim()} data-testid={`q-save-${askId}-${q.key}`}
                    onClick={() => {
                      onSave?.(id, row.value);
                      close(id);
                    }}>
                    Save
                  </button>
                  <button type="button" className={btn} disabled={busy} data-testid={`q-cancel-${askId}-${q.key}`}
                    onClick={() => {
                      onChange(id, editing.get(id) ?? row);
                      close(id);
                    }}>
                    Cancel
                  </button>
                </div>
              </>
            ) : pending ? (
              <>
                <p className="text-sm text-muted-foreground" data-testid={`q-pending-${askId}-${q.key}`}>
                  {notKnown ? "Your assistant could not guess this. Please answer it." : "Your assistant will draft this on its next run"}
                </p>
                <div className="flex gap-2">
                  <button type="button" className={btn} disabled={busy} data-testid={`q-edit-${askId}-${q.key}`} onClick={() => edit(id, row)}>
                    Write it myself
                  </button>
                </div>
              </>
            ) : (
              <>
                <div className="text-sm">
                  {stub ? <DraftMissing id={`${askId}-${q.key}`} value={row.value} /> : <AnswerValue id={`${askId}-${q.key}`} value={row.value} />}
                </div>
                <div className="flex gap-2">
                  <button type="button" className={btn} disabled={busy} data-testid={`q-edit-${askId}-${q.key}`} onClick={() => edit(id, row)}>
                    Edit
                  </button>
                  {!stub && (
                    <button type="button" disabled={busy || row.ok} data-testid={`q-ok-${askId}-${q.key}`}
                      className={row.ok ? picked : btn} onClick={() => {
                        onChange(id, { value: row.value, ok: true });
                        onSave?.(id, row.value);
                      }}>
                      {row.ok ? "Checked" : "Looks right"}
                    </button>
                  )}
                </div>
              </>
            )}
          </div>
        );
      })}
    </>
  );
}
