"use client";

import { useEffect, useState } from "react";
import { getQuestionnaire, rememberQuestionnaire } from "@/lib/api";
import type { Questionnaire as Data, QuestionnaireRow } from "@/lib/api";
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
  if (!(q.value || q.answer || "").trim()) return "Your assistant hasn't guessed this yet";
  const f = (q.from ?? "").trim();
  return f ? f[0].toUpperCase() + f.slice(1) : "Your assistant's guess";
};

const rowKey = (cc: string, q: QuestionnaireRow) => `${cc}:${q.topic}`;

/** The one-time questionnaire on Needs you. Every box starts with the guess; the owner edits it in place.
 *  Nothing is saved (no blur, change or autosave) until the owner presses
 *  "OK" on a row or "OK all and remember"; a right-to-work or salary answer counts for that country only. Silent when empty. */
export function Questionnaire({ onChanged }: { onChanged?: () => void } = {}) {
  const [data, setData] = useState<Data | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  // A failed read just hides the card.
  const read = () => getQuestionnaire().catch(() => null);
  const load = async () => setData(await read());
  useEffect(() => {
    let cancelled = false;
    void getQuestionnaire().catch(() => null).then((d) => {
      if (!cancelled) setData(d);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const countries = (data?.countries ?? []).filter((c) => c.questions.length > 0);
  if (countries.length === 0) return null;

  const valueOf = (cc: string, q: QuestionnaireRow) => values[rowKey(cc, q)] ?? (q.value || q.answer || "");

  async function remember(cc: string, rows: QuestionnaireRow[]) {
    const items = rows
      .map((q) => ({ topic: q.topic ?? q.id, question: q.question, answer: valueOf(cc, q).trim() }))
      .filter((i) => i.answer);
    if (items.length === 0) return;
    setBusy(true);
    try {
      await rememberQuestionnaire(cc, items);
      await load();
      onChanged?.();
    } catch (err) {
      toast.apiError(err, "Couldn't remember those answers");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="flex flex-col gap-4 rounded-xl border border-border bg-card p-5 shadow-card" data-testid="questionnaire">
      <div className="flex flex-col gap-0.5">
        <h2 className="font-heading text-base">Answer once</h2>
        <p className="text-xs text-muted-foreground">
          Forms ask these again and again. Answer them once per country and your assistant fills them for you.
        </p>
      </div>
      {countries.map(({ country, questions }) => (
        <div key={country} className="flex flex-col gap-2" data-testid={`questionnaire-${country}`}>
          <h3 className={label}>{country}</h3>
          {questions.map((q) => (
            <div key={q.topic} className="flex flex-col gap-2 rounded-lg bg-muted/30 p-3" data-testid={`qn-${country}-${q.topic}`}>
              <p className="text-sm font-medium">{q.question}</p>
              <Textarea aria-label={q.question} value={valueOf(country, q)} disabled={busy} placeholder="Type your answer"
                data-testid={`qn-input-${country}-${q.topic}`}
                onChange={(e) => setValues((prev) => ({ ...prev, [rowKey(country, q)]: e.target.value }))} />
              <p className="text-xs text-muted-foreground" data-testid={`qn-from-${country}-${q.topic}`}>{fromLine(q)}</p>
              <div>
                <button type="button" className={btn} disabled={busy || !valueOf(country, q).trim()}
                  data-testid={`qn-ok-${country}-${q.topic}`} onClick={() => void remember(country, [q])}>
                  OK
                </button>
              </div>
            </div>
          ))}
          <div>
            <button type="button" className={mainBtn} disabled={busy || !questions.some((q) => valueOf(country, q).trim())}
              data-testid={`qn-ok-all-${country}`} onClick={() => void remember(country, questions)}>
              OK all and remember
            </button>
          </div>
        </div>
      ))}
    </section>
  );
}
