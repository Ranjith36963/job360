"use client";

import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { approveSend, declineSend } from "@/lib/api";
import { ApiError } from "@/lib/api-error";
import type { ReadyCardData } from "@/lib/api";
import { clock, docText, flagText, readyEyebrow, readySub, sourceChip } from "@/lib/ready-to-send";
import type { ChipTone } from "@/lib/ready-to-send";
import { whoLabel } from "@/lib/event-labels";
import { cn } from "@/lib/utils";

// Every string here (company, title, questions, answers, flags) was written by
// the user's assistants - untrusted. Only ever rendered as React text nodes.

const label = "font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint";
const CHIP: Record<ChipTone, string> = {
  memory: "bg-brand-soft text-brand border-transparent",
  plain: "border-border text-muted-foreground",
  new: "border-dashed border-border text-foreground",
  guess: "border-warning text-warning",
};
const tap = "min-h-11 md:min-h-0";

/** The approval card: the job, the CV and letter, EVERY answer that will go out
 * with where it came from, and Send / Don't send (the user's own click, saved
 * with the time and this page as the place). Used on Needs you and on the
 * application page. */
export function ReadyCard({
  card,
  paused,
  onChanged,
}: {
  card: ReadyCardData;
  paused: boolean;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const preparer = whoLabel(card.filled_by).name;
  const prepared = clock(card.filled_at);

  async function decide(send: boolean) {
    setBusy(true);
    try {
      await (send
        ? approveSend(card.application_id, {
            artifactId: card.cv?.artifact_id,
            formFilledEventId: card.form_filled_event_id,
          })
        : declineSend(card.application_id));
      toast.success(send ? "Saved your yes." : "Saved: don't send.");
      onChanged();
    } catch (err) {
      if (send && card.cv && err instanceof ApiError && err.status === 409) {
        // The CV or the filled form changed since this card loaded: show the new one.
        toast.error("This changed since you looked. Check it again.");
        onChanged();
        return;
      }
      toast.error(send ? "Could not save your yes. Try again." : "Could not save that. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <article
      data-testid={`ready-card-${card.application_id}`}
      className={cn(
        "flex flex-col gap-4 rounded-xl border border-border bg-card p-5 shadow-card",
        paused && "opacity-60",
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <p className="font-mono text-xs text-faint">{readyEyebrow(card)}</p>
          <h3 className="font-heading text-2xl leading-tight">{card.job_title || "Untitled role"}</h3>
          <p className="text-sm text-muted-foreground">{readySub(card)}</p>
        </div>
        {prepared && (
          <span className="rounded-full bg-brand-soft px-2.5 py-0.5 font-mono text-[11.5px] font-medium text-brand">
            {preparer} prepared this {prepared}
          </span>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {([["CV", card.cv], ["Cover letter", card.cover_letter]] as const).map(([name, doc]) => {
          if (!doc) return null;
          const t = docText(name, doc);
          return (
            <span key={name} className="rounded-md border border-border px-2.5 py-1 text-[13px]">
              {t.label} <span className="font-mono text-xs text-muted-foreground">{t.detail}</span>
            </span>
          );
        })}
        {(card.cv || card.cover_letter) && (
          <Link
            href={`/applications/${card.application_id}#section-documents`}
            className={cn(tap, "inline-flex items-center text-xs font-medium text-muted-foreground underline-offset-2 hover:text-foreground hover:underline")}
          >
            Open both
          </Link>
        )}
      </div>

      {card.flags.length > 0 && (
        <ul data-testid={`ready-flags-${card.application_id}`} className="flex flex-col gap-1 rounded-lg bg-warning-soft px-3 py-2 text-[13px] text-warning">
          {card.flags.map((f, i) => (
            <li key={`${f.code}-${i}`}>{flagText(f)}</li>
          ))}
        </ul>
      )}

      {card.answers.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <h4 className={label}>Every answer that will go out</h4>
          <ul className="flex flex-col divide-y divide-border">
            {card.answers.map((a, i) => {
              const chip = sourceChip(a, card.filled_by);
              return (
                <li key={`${a.question}-${i}`} className="grid gap-x-4 gap-y-1 py-2 text-sm sm:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_auto]">
                  <span className="text-muted-foreground">{a.question}</span>
                  <span className="whitespace-pre-wrap break-words">{a.answer || "(blank)"}</span>
                  <span className={cn("self-start whitespace-nowrap rounded-full border px-2 py-px font-mono text-[11px] font-medium", CHIP[chip.tone])}>
                    {chip.text}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          data-testid={`ready-send-${card.application_id}`}
          disabled={busy || paused}
          onClick={() => void decide(true)}
          className={cn(tap, "rounded-lg bg-primary px-4 py-1.5 text-sm font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50")}
        >
          Send
        </button>
        <button
          type="button"
          data-testid={`ready-decline-${card.application_id}`}
          disabled={busy}
          onClick={() => void decide(false)}
          className={cn(tap, "rounded-lg border border-border px-4 py-1.5 text-sm font-medium hover:bg-muted disabled:opacity-50")}
        >
          Don&apos;t send
        </button>
        <span className="text-xs text-faint">Your choice is saved with the time and this page as the place.</span>
      </div>
    </article>
  );
}
