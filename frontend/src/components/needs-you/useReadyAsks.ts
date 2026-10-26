"use client";

import { useState } from "react";
import type { Ask } from "@/lib/api";
import { answerAskQuestions, rememberAnswers, saveAskRowAnswers } from "@/lib/api";
import { initialRow, isStoredSource, mergeQuestions } from "@/lib/job-cards";
import type { RowState } from "./AskQuestions";

/** The open asks of ONE job that list form questions (what a Ready card must show before Send). */
export function openQuestionAsks(asks: Ask[] | undefined): Ask[] {
  return (asks ?? []).filter((a) => a.status === "open" && !a.withdrawn_at && (a.questions?.length ?? 0) > 0);
}

/**
 * Row state for the open asks shown inside a Ready card. A row counts as answered once it is checked
 * (a choice, "Leave blank", OK, or an edit) and has text; a pre-filled consent row is never
 * checked until the owner clicks. `answerAll` answers every ask with its own keys, as the job card does.
 */
export function useReadyAsks(asks: Ask[]) {
  const merged = mergeQuestions(asks);
  const [rows, setRows] = useState<Record<string, RowState>>({});
  const saved = new Map<string, string>();
  for (const ask of asks)
    for (const k of merged.byAsk.get(ask.id) ?? []) {
      const v = ask.answers?.[k.key];
      if (typeof v === "string" && v.trim()) saved.set(k.rowId, v);
    }
  const rowOf = (id: string): RowState => {
    const m = merged.rows.find((r) => r.id === id)!;
    return rows[id] ?? (saved.has(id) ? { value: saved.get(id)!, ok: true } : initialRow(m.q));
  };
  const complete = merged.rows.every((m) => rowOf(m.id).ok && rowOf(m.id).value.trim() !== "");

  async function saveRow(id: string, value: string) {
    const m = merged.rows.find((r) => r.id === id);
    const text = value.trim();
    if (!m || !text) return;
    for (const ask of asks) {
      const keys = (merged.byAsk.get(ask.id) ?? []).filter((k) => k.rowId === id).map((k) => k.key);
      if (keys.length) await saveAskRowAnswers(ask.id, Object.fromEntries(keys.map((k) => [k, text])));
    }
  }

  async function answerAll() {
    const finalOf = (id: string) => rowOf(id).value.trim();
    for (const ask of asks) {
      const given = Object.fromEntries((merged.byAsk.get(ask.id) ?? []).map((k) => [k.key, finalOf(k.rowId)]));
      await answerAskQuestions(ask.id, given);
    }
    await rememberAnswers(
      merged.rows
        .filter((m) => !(isStoredSource(m.q.source) && finalOf(m.id) === m.q.answer.trim()))
        .map((m) => ({ topic: m.q.key || m.id, question: m.q.question, answer: finalOf(m.id) })),
    );
  }

  return {
    merged,
    rows: Object.fromEntries(merged.rows.map((m) => [m.id, rowOf(m.id)])),
    setRow: (id: string, row: RowState) => setRows((prev) => ({ ...prev, [id]: row })),
    complete,
    saveRow,
    answerAll,
  };
}
