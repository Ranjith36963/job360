"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { getSentOnItsOwn, reportWrongFact } from "@/lib/api";
import type { SentOnItsOwnItem } from "@/lib/api";
import { toast } from "@/lib/toast";

// Sent on its own (owner plan 2026-11-02): a LOG of answers and essays that went to an employer without
// the user, each with the sources it was checked against and a "Wrong fact" button. Not a to-do: it never
// counts on Needs you. Every item comes from the server (GET /api/sent-on-its-own); nothing is made up
// here, and the list is silent while the server has none. Answer and source text came from an assistant:
// rendered as React text only.
// Kind "cv" (owner 2026-11-06): a CV that went on a strong review instead of the user's look. The server
// picks 5 a day to check (cv_sample: "Today's check"); Wrong fact on one turns automatic CV sending off.

const label = "font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint";
const btn = "rounded-md border border-border px-2.5 py-1 text-xs font-medium hover:bg-muted/50 disabled:opacity-50";

function kindWord(kind: string): string {
  if (kind === "cv") return "CV sent after a strong review";
  return kind === "essay" ? "Essay" : "Answer";
}

export function SentOnItsOwn() {
  const [items, setItems] = useState<SentOnItsOwnItem[]>([]);
  const [sample, setSample] = useState<number[]>([]);
  const [busy, setBusy] = useState<number | null>(null);
  const [cvOff, setCvOff] = useState(false);

  useEffect(() => {
    let cancelled = false;
    // A failed read shows nothing: this is a log, and the page works without it.
    void getSentOnItsOwn().then(
      (r) => {
        if (cancelled) return;
        setItems(r.items);
        setSample(r.cv_sample ?? []);
      },
      () => {},
    );
    return () => {
      cancelled = true;
    };
  }, []);

  if (items.length === 0) return null;

  async function wrong(item: SentOnItsOwnItem) {
    setBusy(item.id);
    try {
      await reportWrongFact(item.id);
      setItems((prev) => prev.map((i) => (i.id === item.id ? { ...i, reported_wrong: true } : i)));
      if (item.kind === "cv") setCvOff(true);
    } catch (err) {
      toast.apiError(err, "Couldn't report that fact");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section aria-labelledby="sent-on-its-own" className="flex flex-col gap-3" data-testid="sent-on-its-own">
      <div className="flex flex-col gap-0.5">
        <h2 id="sent-on-its-own" className={label}>Sent on its own ({items.length})</h2>
        <p className="text-xs text-muted-foreground">Your assistant sent these without you. Check them; press Wrong fact if one is not true.</p>
        {cvOff && (
          <p role="status" className="text-xs font-medium" data-testid="sent-cv-off">
            Automatic CV sending is now off. Your assistant will show you each CV again before it goes.
          </p>
        )}
      </div>
      <ul className="flex flex-col gap-2">
        {items.map((i) => (
          <li key={i.id} className="flex flex-col gap-2 rounded-lg border border-border bg-card p-3" data-testid={`sent-${i.id}`}>
            <p className="text-xs text-muted-foreground">
              {i.job_company} · {i.job_title} · {kindWord(i.kind)}
              {i.kind === "cv" && sample.includes(i.id) && (
                <span className="ml-2 rounded bg-muted px-1.5 py-0.5 font-medium text-foreground" data-testid={`sent-sample-${i.id}`}>
                  Today&apos;s check
                </span>
              )}
            </p>
            {i.kind !== "cv" && <p className="text-sm font-medium">{i.question}</p>}
            <p className="whitespace-pre-wrap text-sm">{i.answer}</p>
            {i.sources.length > 0 && (
              <details className="text-xs text-muted-foreground" open={i.kind === "cv"}>
                <summary className="cursor-pointer">Sources ({i.sources.length})</summary>
                <ul className="mt-1 flex flex-col gap-1">
                  {i.sources.map((s, n) => (
                    <li key={n}><span className="font-medium">{s.label}:</span> {s.text}</li>
                  ))}
                </ul>
              </details>
            )}
            {i.kind === "cv" && i.application_id != null && (
              <Link href={`/applications/${i.application_id}`} className="text-xs underline" data-testid={`sent-app-${i.id}`}>
                Open the application
              </Link>
            )}
            <div>
              {i.reported_wrong ? (
                <span className="text-xs text-muted-foreground" data-testid={`sent-reported-${i.id}`}>
                  {i.kind === "cv" ? "You reported a wrong fact. Automatic CV sending is off." : "You reported a wrong fact"}
                </span>
              ) : (
                <button type="button" className={btn} disabled={busy === i.id} data-testid={`sent-wrong-${i.id}`} onClick={() => void wrong(i)}>
                  Wrong fact
                </button>
              )}
              {i.kind === "cv" && !i.reported_wrong && (
                <p className="mt-1 text-xs text-muted-foreground">Wrong fact turns automatic CV sending off at once.</p>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
