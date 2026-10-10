"use client";

import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { approveSend, getReadyToSend } from "@/lib/api";
import { ApiError } from "@/lib/api-error";
import type { ReadyCardData } from "@/lib/api";
import { answerHref, flagText, guardSend, rowDetail } from "@/lib/ready-to-send";
import { cn } from "@/lib/utils";

// Company, title and every flag come from the user's assistants - untrusted
// text nodes only.

const tap = "min-h-11 md:min-h-0";
const small = "rounded-md border border-border px-3 py-1 text-xs font-medium hover:bg-muted disabled:opacity-50";

/** "Send all unflagged (N)": the clean rows listed in full, one confirm, then the
 * existing one-application "Send" for each, so every yes is recorded on its own
 * application. Flagged rows are held out and still go one by one. */
export function SendAllCard({
  clean,
  held,
  paused,
  onChanged,
}: {
  clean: ReadyCardData[];
  held: ReadyCardData[];
  paused: boolean;
  onChanged: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const n = clean.length;

  async function sendAll() {
    setBusy(true);
    try {
      // Re-read right before sending: a row whose fill, CV or flags changed is skipped.
      const fresh = await getReadyToSend();
      if (fresh.paused) {
        toast.error("Your assistants are paused. Nothing was sent.");
        return;
      }
      const { go, skipped } = guardSend(clean, fresh.items);
      const failed: ReadyCardData[] = [];
      let sent = 0;
      for (const c of go) {
        try {
          // The server re-checks the CV and the fill: one that changed is 409, nothing saved.
          await approveSend(c.application_id, {
            artifactId: c.cv?.artifact_id,
            formFilledEventId: c.form_filled_event_id,
          });
          sent += 1;
        } catch (err) {
          (err instanceof ApiError && err.status === 409 ? skipped : failed).push(c);
        }
      }
      const names = (rows: ReadyCardData[]) => rows.map((c) => c.job_company || `APP-${c.application_id}`).join(", ");
      const message = [
        `Sent ${sent}.`,
        skipped.length ? `Skipped (changed since you looked): ${names(skipped)}.` : "",
        failed.length ? `Not saved, try again: ${names(failed)}.` : "",
      ]
        .filter(Boolean)
        .join(" ");
      if (failed.length) toast.error(message);
      else toast.success(message);
    } catch {
      toast.error("Could not send these. Try again.");
    } finally {
      setBusy(false);
      setConfirming(false);
      onChanged();
    }
  }

  return (
    <div data-testid="send-all-card" className={cn("flex flex-col gap-4 rounded-xl border border-border bg-card p-5 shadow-card", paused && "opacity-60")}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h3 className="font-heading text-xl leading-tight">Send all unflagged ({n})</h3>
          <p className="text-sm text-muted-foreground">
            These {n} go out exactly as listed. Open any row to see every answer.
          </p>
        </div>
        {!confirming && (
          <button
            type="button"
            data-testid="send-all-open"
            disabled={paused || busy}
            onClick={() => setConfirming(true)}
            className={cn(tap, "rounded-lg bg-primary px-4 py-1.5 text-sm font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50")}
          >
            Send these {n}
          </button>
        )}
      </div>

      {confirming && (
        <div data-testid="send-all-confirm" className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-muted/30 px-3 py-2 text-sm">
          <span>Send these {n} now? Each one is saved as your yes.</span>
          <button type="button" data-testid="send-all-yes" disabled={busy} onClick={() => void sendAll()} className={cn(tap, "rounded-md bg-primary px-3 py-1 text-xs font-semibold text-primary-foreground disabled:opacity-50")}>
            {busy ? "Sending…" : "Send"}
          </button>
          <button type="button" data-testid="send-all-no" disabled={busy} onClick={() => setConfirming(false)} className={cn(tap, small)}>
            Cancel
          </button>
        </div>
      )}

      <ul className="flex flex-col divide-y divide-border">
        {clean.map((c) => (
          <li key={c.application_id} data-testid={`send-all-row-${c.application_id}`} className="flex flex-wrap items-baseline gap-x-4 gap-y-1 py-2 text-sm">
            <span className="min-w-0 flex-1">
              <span className="font-medium">{c.job_company}</span>{" "}
              <span className="text-muted-foreground">{[c.job_title, c.job_location].filter(Boolean).join(" · ")}</span>
            </span>
            <span className="font-mono text-xs text-muted-foreground">{rowDetail(c)}</span>
            <Link href={`/applications/${c.application_id}`} className={cn(tap, "inline-flex items-center text-xs font-medium text-muted-foreground underline-offset-2 hover:text-foreground hover:underline")}>
              Open
            </Link>
          </li>
        ))}
      </ul>

      {held.map((c) => (
        <div key={c.application_id} data-testid={`held-row-${c.application_id}`} className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-3">
          <div className="flex min-w-0 flex-col gap-0.5">
            <p className="font-mono text-xs text-faint">
              Held out · {[c.job_company, c.job_title, c.job_location].filter(Boolean).join(" · ")}
            </p>
            <p className="text-sm text-warning">{flagText(c.flags[0])}</p>
          </div>
          <Link href={answerHref(c)} data-testid={`held-answer-${c.application_id}`} className={cn(tap, small, "inline-flex items-center")}>
            Answer
          </Link>
        </div>
      ))}
    </div>
  );
}
