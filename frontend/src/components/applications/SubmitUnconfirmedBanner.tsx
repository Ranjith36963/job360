"use client";

import { useState } from "react";
import { toast } from "sonner";
import { recordApplicationReceipt, resolveSubmit } from "@/lib/api";
import { apiErrorMessage } from "@/lib/api-error";

/** "Started, never confirmed": the assistant began a submit and nothing confirmed it.
 *  Two honest answers: it was sent (record the application, same as Mark Applied) or it
 *  was not (the server closes the stop). Shows only while the page says it is unconfirmed. */
export function SubmitUnconfirmedBanner({
  applicationId,
  onChanged,
}: {
  applicationId: number;
  onChanged: () => Promise<void> | void;
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
      toast.error(apiErrorMessage(err, failMsg));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      role="alert"
      data-testid="submit-unconfirmed"
      className="flex flex-col gap-3 rounded-md border border-warning/40 bg-warning/10 p-4 text-sm"
    >
      <p>
        Your assistant started submitting this and it was never confirmed. Check the employer&apos;s email or site.
      </p>
      {confirming ? (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span>Record that you applied? This creates a receipt that can&apos;t be deleted.</span>
          <button
            type="button"
            data-testid="submit-sent-confirm"
            disabled={busy}
            onClick={() => void run(() => recordApplicationReceipt(applicationId, {}), "Could not record this application.")}
            className="rounded-md bg-primary px-2.5 py-1 text-xs font-semibold text-primary-foreground disabled:opacity-50"
          >
            {busy ? "Recording…" : "Confirm"}
          </button>
          <button
            type="button"
            data-testid="submit-sent-cancel"
            disabled={busy}
            onClick={() => setConfirming(false)}
            className="rounded-md border border-border px-2.5 py-1 text-xs font-medium text-muted-foreground disabled:opacity-50"
          >
            Cancel
          </button>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            data-testid="submit-was-sent"
            disabled={busy}
            onClick={() => setConfirming(true)}
            className="rounded-full bg-primary px-3 py-1 text-xs font-semibold text-primary-foreground disabled:opacity-50"
          >
            It was sent
          </button>
          <button
            type="button"
            data-testid="submit-not-sent"
            disabled={busy}
            onClick={() => void run(() => resolveSubmit(applicationId), "Could not save that. Try again.")}
            className="rounded-full border border-border px-3 py-1 text-xs font-medium text-foreground disabled:opacity-50"
          >
            {busy ? "Saving…" : "It was not sent"}
          </button>
        </div>
      )}
    </section>
  );
}
