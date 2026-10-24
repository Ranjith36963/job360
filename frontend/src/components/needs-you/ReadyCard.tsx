"use client";

import Link from "next/link";
import { CheckCheck, Pencil } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { approveSend, confirmAnswer, declineSend } from "@/lib/api";
import { startAssistantAfterSend } from "@/lib/assistant-start";
import { ApiError } from "@/lib/api-error";
import type { ReadyAnswer, ReadyCardData } from "@/lib/api";
import { canCheck, clock, docText, flagText, heldText, readyEyebrow, readySub, sourceChip } from "@/lib/ready-to-send";
import { FinishInBrowser } from "@/components/applications/FinishInBrowser";
import type { ChipTone } from "@/lib/ready-to-send";
import { whoLabel } from "@/lib/event-labels";
import { nothingSent } from "@/lib/application-actions";
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
  status = "considering",
  changes,
}: {
  /** The application status: "Nothing has been sent" shows while the table says so. */
  status?: string;
  /** CV changes not yet approved; unknown (e.g. the Needs-you list) shows no count. */
  changes?: number;
  card: ReadyCardData;
  paused: boolean;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const preparer = whoLabel(card.filled_by).name;
  const prepared = clock(card.filled_at);
  // Answers the user edited: the yes still goes through, and the assistant types them before it sends.
  const changed = card.answers.filter((a) => a.changed_to != null).length;

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
      if (send) void startAssistantAfterSend();
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
            {card.answers.map((a, i) => (
              <AnswerRow
                key={`${a.question}-${i}`}
                answer={a}
                index={i}
                card={card}
                onSaved={onChanged}
              />
            ))}
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
        {card.held_seconds != null && card.held_seconds > 0 && !finishing && (
          <button
            type="button"
            data-testid={`ready-finish-${card.application_id}`}
            onClick={() => setFinishing(true)}
            className={cn(tap, "rounded-lg border border-border px-4 py-1.5 text-sm font-medium hover:bg-muted")}
          >
            {`Finish it here${heldText(card.held_seconds)}`}
          </button>
        )}
        {nothingSent(status) && (
          <p data-testid="nothing-sent" className="text-xs text-muted-foreground">
            {changes === undefined ? (
              "Nothing sent yet"
            ) : (
              <>
                <span data-testid="changes-since-approved">{`${changes} change${changes === 1 ? "" : "s"}`}</span> · nothing sent yet
              </>
            )}
          </p>
        )}
        {changed > 0 ? (
          <span data-testid={`ready-changed-${card.application_id}`} className="text-xs text-info">
            {`You changed ${changed} answer${changed === 1 ? "" : "s"}. Your assistant types ${changed === 1 ? "it" : "them"} in before it sends.`}
          </span>
        ) : (
          <span className="text-xs text-faint">Your choice is saved with the time and this page as the place.</span>
        )}
      </div>
      {/* The filled form, kept open in Job360's browser: the user can look at it and press Submit themselves. */}
      {finishing && (
        <FinishInBrowser
          applicationId={card.application_id}
          onDone={() => {
            setFinishing(false);
            onChanged();
          }}
        />
      )}
    </article>
  );
}

/** One answer: what was typed, where it came from, and - for a memory answer or a
 * guess - Verify (the user stands behind it, blue double tick) or Edit (the right
 * answer). Both save the user's word in memory so the assistant learns it. */
function AnswerRow({
  answer: a,
  index,
  card,
  onSaved,
}: {
  answer: ReadyAnswer;
  index: number;
  card: ReadyCardData;
  onSaved: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const chip = sourceChip(a, card.filled_by);
  const checkable = canCheck(a);
  const id = `${card.application_id}-${index}`;

  async function save(text?: string) {
    setSaving(true);
    try {
      await confirmAnswer(card.application_id, index, card.form_filled_event_id, text);
      toast.success(text === undefined ? "Verified. Saved as your answer." : "Saved. Your assistant will use it next time.");
      setEditing(false);
      onSaved();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        toast.error("This changed since you looked. Check it again.");
        onSaved();
        return;
      }
      toast.error(err instanceof ApiError && err.status === 422 ? err.message : "Could not save that. Try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <li data-testid={`ready-answer-${id}`} className="grid gap-x-4 gap-y-1 py-2 text-sm sm:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_auto]">
      <span className="text-muted-foreground">{a.question}</span>
      {editing ? (
        <div className="flex flex-col gap-2">
          <textarea
            data-testid={`ready-answer-input-${id}`}
            aria-label={`Your answer to: ${a.question}`}
            value={draft}
            maxLength={2000}
            rows={2}
            onChange={(e) => setDraft(e.target.value)}
            className="w-full rounded-md border border-border bg-background px-2 py-1 text-sm"
          />
          <div className="flex gap-2">
            <button
              type="button"
              data-testid={`ready-answer-save-${id}`}
              disabled={saving || !draft.trim()}
              onClick={() => void save(draft)}
              className={cn(tap, "rounded-md bg-primary px-3 py-1 text-xs font-semibold text-primary-foreground disabled:opacity-50")}
            >
              Save
            </button>
            <button
              type="button"
              disabled={saving}
              onClick={() => setEditing(false)}
              className={cn(tap, "rounded-md border border-border px-3 py-1 text-xs font-medium hover:bg-muted disabled:opacity-50")}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : a.changed_to != null ? (
        <span className="flex flex-col gap-0.5 break-words">
          <span className="text-muted-foreground line-through">{a.answer || "(blank)"}</span>
          <span data-testid={`ready-answer-changed-${id}`} className="whitespace-pre-wrap font-medium">
            {a.changed_to}
          </span>
          <span className="text-xs text-info">Your answer. Your assistant types it in before it sends.</span>
        </span>
      ) : (
        <span className="whitespace-pre-wrap break-words">
          {a.redacted ? "(not kept: sensitive - Edit to type it)" : a.answer || "(blank)"}
        </span>
      )}
      <span className="flex flex-wrap items-start gap-1.5 sm:justify-end">
        <span className={cn("whitespace-nowrap rounded-full border px-2 py-px font-mono text-[11px] font-medium", CHIP[chip.tone])}>
          {chip.text}
        </span>
        {a.verified && (
          <span
            data-testid={`ready-answer-verified-${id}`}
            className="inline-flex items-center gap-1 whitespace-nowrap rounded-full bg-info-soft px-2 py-px font-mono text-[11px] font-medium text-info"
          >
            <CheckCheck aria-hidden className="size-3.5" />
            Verified
          </span>
        )}
        {checkable && !editing && (
          <>
            {!a.verified && a.changed_to == null && a.answer && (
              <button
                type="button"
                data-testid={`ready-answer-verify-${id}`}
                disabled={saving}
                onClick={() => void save()}
                aria-label={`Verify: ${a.question}`}
                className={cn(tap, "inline-flex items-center gap-1 rounded-full border border-info px-2 py-px text-[11px] font-medium text-info hover:bg-info-soft disabled:opacity-50")}
              >
                <CheckCheck aria-hidden className="size-3.5" />
                Verify
              </button>
            )}
            <button
              type="button"
              data-testid={`ready-answer-edit-${id}`}
              disabled={saving}
              onClick={() => {
                setDraft(a.changed_to ?? a.answer ?? "");
                setEditing(true);
              }}
              aria-label={`Edit: ${a.question}`}
              className={cn(tap, "inline-flex items-center gap-1 rounded-full border border-border px-2 py-px text-[11px] font-medium text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50")}
            >
              <Pencil aria-hidden className="size-3" />
              Edit
            </button>
          </>
        )}
      </span>
    </li>
  );
}
