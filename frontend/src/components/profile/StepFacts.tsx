"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { factRoom, factsFor, withFact, withoutFact } from "@/lib/step-facts";

/** "Your own facts" at the foot of a Memory step: what the user wants the
 *  assistant to know for this step, read before every form. The user wrote
 *  them, so they need no confirming. */
export function StepFacts({ id, step, notes, onSave }: {
  id: string; step: string; notes: string[];
  /** Gets a change to apply to the notes as last saved (never a stale copy). */
  onSave: (change: (notes: string[]) => string[]) => Promise<void>;
}) {
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const facts = factsFor(notes, step);
  const room = factRoom(step);

  const run = async (change: (notes: string[]) => string[], after?: () => void) => {
    setBusy(true);
    setFailed(false);
    try {
      await onSave(change);
      after?.();
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div data-testid={`step-facts-${id}`} className="mt-3 border-t border-dashed border-border pt-3">
      <p className="text-[13px] font-medium">Your own facts</p>
      <p className="mb-2 text-[12px] text-muted-foreground">Anything else your assistant should know here. It reads these before every form.</p>
      {facts.length > 0 && (
        <ul className="mb-2 flex flex-col gap-1">
          {facts.map((fact, i) => (
            <li key={`${i}-${fact}`} className="flex items-start justify-between gap-3 text-sm">
              <span className="min-w-0 break-words">{fact}</span>
              <Button type="button" variant="ghost" size="sm" className="min-h-11 md:min-h-7" disabled={busy}
                aria-label={`Remove "${fact}"`} onClick={() => void run((n) => withoutFact(n, step, factsFor(n, step).indexOf(fact)))}>
                Remove
              </Button>
            </li>
          ))}
        </ul>
      )}
      <form className="flex gap-2" onSubmit={(e) => {
        e.preventDefault();
        if (!draft.trim()) return;
        const fact = draft;
        void run((n) => withFact(n, step, fact), () => setDraft(""));
      }}>
        <input value={draft} onChange={(e) => setDraft(e.target.value)} maxLength={room} disabled={busy}
          aria-label={`Add a fact to ${step}`} placeholder="Add a fact"
          className="min-h-11 min-w-0 flex-1 rounded-md border border-border bg-background px-3 text-sm md:min-h-8" />
        <Button type="submit" size="sm" variant="outline" className="min-h-11 md:min-h-8" disabled={busy || !draft.trim()}>
          Add
        </Button>
      </form>
      {failed && <p role="alert" className="mt-1 text-xs text-destructive">Could not save. Try again.</p>}
    </div>
  );
}
