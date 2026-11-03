"use client";

import { useState } from "react";
import type { Ask } from "@/lib/api";
import { answerAsk, recordApplicationReceipt } from "@/lib/api";
import { toast } from "@/lib/toast";
import { openItemsText } from "@/lib/open-items";

const btn =
  "rounded-md border border-border px-2.5 py-1 text-xs font-medium hover:bg-muted/50 disabled:opacity-50";
const mainBtn =
  "rounded-md bg-primary px-2.5 py-1 text-xs font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50";

/** "The site says you already applied. Mark it applied?" Two buttons, no text box: typing "yes" would
 *  only store an answer, while only Mark Applied (the existing receipt call, behind the same confirm
 *  box that lists open items) records the application. "I didn't apply" answers the ask "no". */
export function AlreadyAppliedRow({
  ask,
  applicationId,
  openItems,
  onChanged,
}: {
  ask: Ask;
  applicationId: number;
  openItems: string[];
  onChanged: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);

  async function run(fn: () => Promise<unknown>, failMsg: string) {
    setBusy(true);
    try {
      await fn();
      setConfirming(false);
      await onChanged();
    } catch (err) {
      toast.apiError(err, failMsg);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-2 rounded-lg bg-muted/30 p-3" data-testid={`already-applied-${ask.id}`}>
      <p className="text-sm font-medium">{ask.question}</p>
      {confirming ? (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {openItems.length > 0 && (
            <p className="basis-full text-warning" data-testid="mark-applied-open-items">
              {openItemsText(openItems)}
            </p>
          )}
          <span>Record that you applied? This creates a receipt that can&apos;t be deleted.</span>
          <button type="button" className={mainBtn} disabled={busy} data-testid={`already-applied-confirm-${ask.id}`}
            onClick={() => void run(() => recordApplicationReceipt(applicationId, {}), "Could not record this application.")}>
            {busy ? "Recording…" : "Yes, record it"}
          </button>
          <button type="button" className={btn} disabled={busy} onClick={() => setConfirming(false)}>
            Cancel
          </button>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          <button type="button" className={mainBtn} disabled={busy} data-testid={`already-applied-mark-${ask.id}`}
            onClick={() => setConfirming(true)}>
            Mark applied
          </button>
          <button type="button" className={btn} disabled={busy} data-testid={`already-applied-no-${ask.id}`}
            onClick={() => void run(() => answerAsk(ask.id, "no"), "Couldn't save that")}>
            I didn&apos;t apply
          </button>
        </div>
      )}
    </div>
  );
}
