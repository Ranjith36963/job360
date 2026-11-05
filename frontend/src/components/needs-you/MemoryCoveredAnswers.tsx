"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { deleteCoveredAnswer, getCoveredAnswers } from "@/lib/api";
import type { CoveredAnswer } from "@/lib/api";
import { toast } from "@/lib/toast";
import { placeName } from "@/components/profile/CountryPages";

// Memory first (owner 2026-11-05, plan S3): saved answers from before Memory answered these questions.
// Forms no longer use them; the owner deletes each one himself. Question and answer text may come from the
// assistant: untrusted, rendered as React text only.

const btn = "rounded-md border border-border px-2.5 py-1 text-xs font-medium hover:bg-muted/50 disabled:opacity-50";
const dangerBtn =
  "rounded-md bg-destructive/10 px-2.5 py-1 text-xs font-semibold text-destructive hover:bg-destructive/20 disabled:opacity-50";
const linkCls = "text-xs font-medium underline underline-offset-2 hover:text-foreground";

const VERDICT: Record<CoveredAnswer["verdict"], string> = {
  same: "Says the same as Memory",
  different: "Says something different from Memory",
  memory_empty: "Memory has nothing for this yet",
};

const where = (cc: string | null | undefined) => (cc ? placeName(cc) : "no country");

/** One card on Needs you: each old saved answer beside what Memory says, with its own Delete (asks first).
 *  Nothing is deleted without that click. Silent when there are none or the read fails. */
export function MemoryCoveredAnswers({ onChanged }: { onChanged?: () => void } = {}) {
  const [rows, setRows] = useState<CoveredAnswer[] | null>(null);
  const [asking, setAsking] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void getCoveredAnswers().then((d) => d.answers, () => null).then((r) => {
      if (!cancelled) setRows(r);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!rows || rows.length === 0) return null;

  async function remove(id: string) {
    setBusy(true);
    try {
      await deleteCoveredAnswer(id);
      setRows(await getCoveredAnswers().then((d) => d.answers, () => (rows ?? []).filter((r) => r.id !== id)));
      setAsking(null);
      toast.success("Deleted.");
      onChanged?.();
    } catch (err) {
      toast.apiError(err, "Couldn't delete that answer");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="flex flex-col gap-4 rounded-xl border border-border bg-card p-5 shadow-card" data-testid="memory-covered">
      <div className="flex flex-col gap-0.5">
        <h2 className="font-heading text-base">Old saved answers that Memory now covers</h2>
        <p className="text-xs text-muted-foreground">
          Forms now take these answers from Memory, so the old saved ones are no longer used. Check each one and delete
          it when you are happy with what Memory says.
        </p>
      </div>
      {rows.map((r) => (
        <div key={r.id} className="flex flex-col gap-1.5 rounded-lg bg-muted/30 p-3 text-sm" data-testid={`covered-${r.id}`}>
          <p className="font-medium">{r.question}</p>
          <p>
            <span className="text-muted-foreground">Saved answer ({where(r.country)}): </span>
            <span data-testid={`covered-saved-${r.id}`}>{r.answer}</span>
          </p>
          <p>
            <span className="text-muted-foreground">Memory says: </span>
            <span data-testid={`covered-memory-${r.id}`}>{r.memory ?? "nothing yet"}</span>
          </p>
          <p className="text-xs text-muted-foreground" data-testid={`covered-verdict-${r.id}`}>{VERDICT[r.verdict]}</p>
          {r.verdict === "memory_empty" && (
            <p className="text-xs text-muted-foreground">
              <Link href="/profile/memory" className={linkCls} data-testid={`covered-set-${r.id}`}>
                Set it in Memory{r.country ? ` (${where(r.country)})` : ""}
              </Link>{" "}
              before you delete this one.
            </p>
          )}
          {asking === r.id ? (
            <div className="flex flex-wrap items-center gap-2 text-xs" data-testid={`covered-confirm-${r.id}`}>
              <span>Delete this saved answer? This can&apos;t be undone here.</span>
              <button type="button" className={dangerBtn} disabled={busy} data-testid={`covered-yes-${r.id}`}
                onClick={() => void remove(r.id)}>
                Yes, delete
              </button>
              <button type="button" className={btn} disabled={busy} onClick={() => setAsking(null)}>
                Cancel
              </button>
            </div>
          ) : (
            <div>
              <button type="button" className={btn} disabled={busy} data-testid={`covered-delete-${r.id}`}
                onClick={() => setAsking(r.id)}>
                Delete
              </button>
            </div>
          )}
        </div>
      ))}
    </section>
  );
}
