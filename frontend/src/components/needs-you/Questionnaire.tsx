"use client";

import { useEffect, useRef, useState } from "react";
import { getQuestionnaire, rememberQuestionnaire } from "@/lib/api";
import type { Questionnaire as Data, QuestionnaireRow } from "@/lib/api";
import { regionName } from "@/lib/regions";
import { toast } from "@/lib/toast";
import { Textarea } from "@/components/ui/textarea";

// Ask once (owner 2026-11-02): one card, up to 10 common questions per country, answered once. Question
// text and drafted answers come from the user's assistant: untrusted, rendered as React text only.

const label = "font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint";
const btn = "rounded-md border border-border px-2.5 py-1 text-xs font-medium hover:bg-muted/50 disabled:opacity-50";
const mainBtn =
  "rounded-lg bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50";

/** Where the box text came from, in plain words. Empty box = no guess yet. */
const fromLine = (q: QuestionnaireRow) => {
  if (conflictOf(q).length) return "Pick the right one";
  if (!(q.value || q.answer || "").trim()) return "Your assistant hasn't guessed this yet";
  const f = (q.from ?? "").trim();
  return f ? f[0].toUpperCase() + f.slice(1) : "Your assistant's guess";
};

/** The values a row's saved answers disagree on; [] = no conflict. Server text, never trusted. */
const conflictOf = (q: QuestionnaireRow) => (q.conflict ?? []).filter((v) => v.trim());

/** "You saved both Yes and No for this question in GB — which is right?" No topic word is made up. */
const conflictLine = (vals: string[], cc: string) => {
  const place = placeName(cc);
  const list = vals.length === 2 ? `both ${vals[0]} and ${vals[1]}` : `${vals.slice(0, -1).join(", ")} and ${vals[vals.length - 1]}`;
  return `You saved ${list} for this question in ${place} — which is right?`;
};

/** Memory rows take one exact value (owner bug 2026-11-18: "yes" to a work-status question saved nothing,
 *  silently). These rows offer the values Memory stores as one tap each. */
const WORK_STATUS = [["Citizen", "citizen"], ["Permanent resident", "permanent resident"], ["Visa", "visa"],
  ["Need sponsorship", "needs sponsorship"]] as const;
const YES_NO = [["Yes", "Yes"], ["No", "No"]] as const;
const choicesOf = (topic: string): readonly (readonly [string, string])[] => {
  const field = topic.split(".").pop() ?? "";
  if (field === "work_authorization") return WORK_STATUS;
  return ["needs_sponsorship", "willing_to_relocate", "onsite"].includes(field) ? YES_NO : [];
};

/** Why the server did not keep an answer, in plain words and how to say it so it does. */
const notSavedLine = (topic: string, place: string) => {
  const field = topic.split(".").pop() ?? "";
  if (topic.startsWith("salary.")) return "Not saved yet: add a currency, e.g. 70000-120000 USD a year";
  if (field === "travel_ok_pct") return "Not saved yet: say how much as a percent, e.g. 25 (or No)";
  if (field === "work_authorization") return "Not saved yet: pick one of the buttons";
  if (choicesOf(topic).length) return "Not saved yet: answer just Yes or No";
  return `Not saved: set this on your ${place} page in Memory`;
};

/** A place as people say it: a region by its name ("North America"), a country by its code. */
const placeName = (code: string) => regionName(code) ?? code;

/** A row's save state, shown under it. */
type Status = { state: "saving" | "saved" | "not_saved" | "failed"; text?: string };
const SAVE_AFTER_MS = 1000;

const rowKey = (cc: string, q: QuestionnaireRow) => `${cc}:${q.topic}`;

/** The one-time questionnaire on Needs you. Every box starts with the guess; the owner edits it in place.
 *  Owner 2026-11-18: like the profile, a typed answer saves itself a second after the typing stops (and when
 *  the box is left); each row says Saved, or why not and how to say it. The one button left confirms the
 *  assistant's guesses the owner did not touch. A right-to-work or salary answer counts for that place only. */
export function Questionnaire({ onChanged }: { onChanged?: () => void } = {}) {
  const [data, setData] = useState<Data | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<Record<string, Status>>({});
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  useEffect(() => {
    let cancelled = false;
    const pending = timers.current;
    void getQuestionnaire().catch(() => null).then((d) => {
      if (!cancelled) setData(d);
    });
    return () => {
      cancelled = true;
      Object.values(pending).forEach(clearTimeout);
    };
  }, []);

  const countries = (data?.countries ?? []).filter((c) => c.questions.length > 0);
  if (countries.length === 0) return null;

  const valueOf = (cc: string, q: QuestionnaireRow) => values[rowKey(cc, q)] ?? (conflictOf(q).length ? "" : q.value || q.answer || "");

  /** Save these rows' answers for this place and mark each row with what the server did. */
  async function save(cc: string, rows: { q: QuestionnaireRow; answer: string }[]) {
    const items = rows
      .map(({ q, answer }) => ({ q, item: { topic: q.topic ?? q.id, question: q.question, answer: answer.trim() } }))
      .filter(({ item }) => item.answer);
    if (items.length === 0) return;
    const mark = (next: Record<string, Status>) => setStatus((prev) => ({ ...prev, ...next }));
    mark(Object.fromEntries(items.map(({ q }) => [rowKey(cc, q), { state: "saving" as const }])));
    try {
      const out = await rememberQuestionnaire(cc, items.map((i) => i.item));
      const kept = new Set([...(out.saved ?? []), ...Object.keys(out.memory ?? {})]);
      mark(Object.fromEntries(items.map(({ q, item }): [string, Status] => {
        const conflict = out.conflicts?.[item.topic];
        if (kept.has(item.topic)) return [rowKey(cc, q), { state: "saved" }];
        if (conflict) return [rowKey(cc, q), { state: "not_saved", text: conflict }];
        return [rowKey(cc, q), { state: "not_saved", text: notSavedLine(item.topic, placeName(cc)) }];
      })));
      if (kept.size) onChanged?.();
    } catch (err) {
      mark(Object.fromEntries(items.map(({ q }) => [rowKey(cc, q), { state: "failed" as const, text: "Couldn't save. Try again." }])));
      toast.apiError(err, "Couldn't save that answer");
    }
  }

  /** The owner's own typing: the row saves a second after the last key (or at once when the box is left). */
  function typed(cc: string, q: QuestionnaireRow, text: string, now = false) {
    const key = rowKey(cc, q);
    setValues((prev) => ({ ...prev, [key]: text }));
    clearTimeout(timers.current[key]);
    if (!text.trim()) return;
    if (now) {
      void save(cc, [{ q, answer: text }]);
      return;
    }
    timers.current[key] = setTimeout(() => void save(cc, [{ q, answer: text }]), SAVE_AFTER_MS);
  }

  function left(cc: string, q: QuestionnaireRow) {
    const key = rowKey(cc, q);
    if (!(key in timers.current) || !(key in values)) return;
    clearTimeout(timers.current[key]);
    delete timers.current[key];
    if (status[key]?.state !== "saved" && status[key]?.state !== "saving") void save(cc, [{ q, answer: values[key] }]);
  }

  return (
    <section className="flex flex-col gap-4 rounded-xl border border-border bg-card p-5 shadow-card" data-testid="questionnaire">
      <div className="flex flex-col gap-0.5">
        <h2 className="font-heading text-base">Answer once</h2>
        <p className="text-xs text-muted-foreground">
          Forms ask these again and again. Answer them once per country and your assistant fills them for you. Each answer saves as you type.
        </p>
      </div>
      {countries.map(({ country, questions }) => {
        // The assistant's guesses the owner has not touched or saved: the one button confirms them.
        const guesses = questions.filter((q) => q.label === "guessed" && !conflictOf(q).length
          && !(rowKey(country, q) in values) && !status[rowKey(country, q)] && (q.value || q.answer || "").trim());
        return (
        <div key={country} className="flex flex-col gap-2" data-testid={`questionnaire-${country}`}>
          <h3 className={label} data-testid={`questionnaire-name-${country}`}>{placeName(country)}</h3>
          {questions.map((q) => {
            const key = rowKey(country, q);
            const st = status[key];
            const choices = conflictOf(q).length ? conflictOf(q).map((v) => [v, v] as const) : choicesOf(q.topic ?? "");
            return (
            <div key={q.topic} className="flex flex-col gap-2 rounded-lg bg-muted/30 p-3" data-testid={`qn-${country}-${q.topic}`}>
              {conflictOf(q).length > 0 && (
                <p className="text-sm" data-testid={`qn-conflict-${country}-${q.topic}`}>{conflictLine(conflictOf(q), country)}</p>
              )}
              <p className="text-sm font-medium">{q.question}</p>
              <Textarea aria-label={q.question} value={valueOf(country, q)} placeholder="Type your answer"
                data-testid={`qn-input-${country}-${q.topic}`}
                onChange={(e) => typed(country, q, e.target.value)} onBlur={() => left(country, q)} />
              {choices.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {choices.map(([shown, said], i) => (
                    <button key={`${i}-${said}`} type="button" className={btn} data-testid={`qn-use-${country}-${q.topic}-${i}`}
                      aria-pressed={valueOf(country, q) === said} onClick={() => typed(country, q, said, true)}>
                      {conflictOf(q).length ? `Use ${shown}` : shown}
                    </button>
                  ))}
                </div>
              )}
              <p className={`text-xs ${st?.state === "not_saved" || st?.state === "failed" ? "text-destructive" : "text-muted-foreground"}`}
                data-testid={`qn-from-${country}-${q.topic}`} aria-live="polite">
                {st?.state === "saving" ? "Saving…" : st?.state === "saved" ? "Saved ✓" : st?.text ?? fromLine(q)}
              </p>
            </div>
            );
          })}
          {guesses.length > 0 && (
            <div>
              <button type="button" className={mainBtn} data-testid={`qn-ok-all-${country}`}
                onClick={() => void save(country, guesses.map((q) => ({ q, answer: valueOf(country, q) })))}>
                {guesses.length === 1 ? "This guess is right" : `These ${guesses.length} guesses are right`}
              </button>
            </div>
          )}
        </div>
        );
      })}
    </section>
  );
}
