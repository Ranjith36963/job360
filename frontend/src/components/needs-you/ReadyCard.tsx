"use client";

import Link from "next/link";
import { Check, Pencil } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { approveSend, confirmAnswer, declineSend } from "@/lib/api";
import { startAssistantAfterSend } from "@/lib/assistant-start";
import { ApiError } from "@/lib/api-error";
import type { Ask, ReadyAnswer, ReadyCardData } from "@/lib/api";
import { canCheck, clock, docText, flagText, heldText, readyEyebrow, readySub } from "@/lib/ready-to-send";
import { isDraftStub, readyRowLabel } from "@/lib/answer-status";
import { SourceLine, StatusWord } from "./AnswerStatus";
import { FinishInBrowser } from "@/components/applications/FinishInBrowser";
import { whoLabel } from "@/lib/event-labels";
import { nothingSent } from "@/lib/application-actions";
import { cn } from "@/lib/utils";
import { readyBrowser } from "@/lib/browser-contract";
import { BrowserReason } from "./BrowserReason";
import { AnswerText } from "@/components/AnswerText";
import { AnswerValue, DraftMissing } from "./AnswerValue";
import { AskQuestions } from "./AskQuestions";
import { openQuestionAsks, useReadyAsks } from "./useReadyAsks";
import { suggestSalary } from "@/lib/salary-suggest";
import type { SalaryFacts } from "@/lib/salary-suggest";

// Every string here (company, title, questions, answers, flags) was written by
// the user's assistants - untrusted. Only ever rendered as React text nodes.

const label = "font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint";
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
  asks,
  salaryFacts,
}: {
  /** The user's saved salary facts (from Memory), read once by the page: they feed the suggested salary answers. */
  salaryFacts?: SalaryFacts;
  /** This job's open asks: the ones with form questions show inline and must be answered before Send. */
  asks?: Ask[];
  /** The application status: "Nothing has been sent" shows while the table says so. */
  status?: string;
  /** CV changes not yet approved; unknown (e.g. the Needs-you list) shows no count. */
  changes?: number;
  card: ReadyCardData;
  paused: boolean;
  onChanged: () => void;
}) {
  const openAsks = openQuestionAsks(asks);
  const ra = useReadyAsks(openAsks);
  const askBlocked = openAsks.length > 0 && !ra.complete;
  const [busy, setBusy] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const preparer = whoLabel(card.filled_by).name;
  const prepared = clock(card.filled_at);
  // Answers that only describe a draft: they count as needing attention here (the backend gate is unchanged).
  const missing = card.answers.filter(isDraftStub).length;

  async function decide(send: boolean) {
    setBusy(true);
    try {
      // Never approve a job whose open ask is unanswered: answer it first (the owner's own click on each row).
      if (send && openAsks.length > 0) {
        if (!ra.complete) return;
        await ra.answerAll();
      }
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
          <h3 className="font-heading text-2xl leading-tight">
            <Link href={`/applications/${card.application_id}`} data-testid={`title-link-${card.application_id}`}
              className="underline-offset-2 hover:underline">
              {card.job_title || "Untitled role"}
            </Link>
          </h3>
          <p className="text-sm text-muted-foreground">
            {card.job_company ? (
              <Link href={`/applications/${card.application_id}`} data-testid={`company-link-${card.application_id}`}
                className="underline-offset-2 hover:underline">{card.job_company}</Link>
            ) : null}
            {readySub(card).slice(card.job_company ? card.job_company.length : 0) || (card.job_company ? "" : readySub(card))}
          </p>
          {card.form_open === false && (
            <p data-testid="ready-form-closed" className="text-[13px] text-muted-foreground">
              The form was closed. Press Send and it will be filled again, then sent.
            </p>
          )}
        </div>
        {prepared && (
          <span className="rounded-full bg-brand-soft px-2.5 py-0.5 font-mono text-[11.5px] font-medium text-brand">
            {preparer} prepared this {prepared}
          </span>
        )}
      </div>

      {readyBrowser(card) && (
        <div className="flex flex-col gap-0.5" data-testid={`ready-browser-${card.application_id}`}>
          <span className="w-fit rounded bg-muted px-1.5 py-0.5 text-[11px] font-medium text-muted-foreground"
            data-testid={`ready-browser-label-${card.application_id}`}>{readyBrowser(card)!.label}</span>
          <BrowserReason label={readyBrowser(card)!} testid={`ready-browser-reason-${card.application_id}`} />
        </div>
      )}

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

      {openAsks.length > 0 && (
        <section data-testid="ready-ask-questions" className="flex flex-col gap-2">
          <h4 className={label}>Your assistant also needs these answered before you send</h4>
          <AskQuestions
            items={ra.merged.rows}
            rows={ra.rows}
            busy={busy}
            onChange={ra.setRow}
            onSave={(id, value) => {
              setBusy(true);
              ra.saveRow(id, value)
                .catch(() => toast.error("Could not save that answer. Try again."))
                .finally(() => setBusy(false));
            }}
          />
          {askBlocked && (
            <p data-testid="ready-ask-blocked" className="text-xs text-muted-foreground">
              Answer every question above to turn on Send.
            </p>
          )}
        </section>
      )}

      {(card.flags.length > 0 || missing > 0) && (
        <ul data-testid={`ready-flags-${card.application_id}`} className="flex flex-col gap-1 rounded-lg bg-warning-soft px-3 py-2 text-[13px] text-warning">
          {card.flags.map((f, i) => (
            <li key={`${f.code}-${i}`}>{flagText(f)}</li>
          ))}
          {missing > 0 && (
            <li data-testid={`ready-draft-missing-${card.application_id}`}>
              {`${missing} answer${missing === 1 ? "" : "s"} still need${missing === 1 ? "s" : ""} the draft text written.`}
            </li>
          )}
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
                salaryFacts={salaryFacts}
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
          disabled={busy || paused || askBlocked}
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

/** The user's own edit after this fill - the only answer the card says "You changed" about. A
 * missing ``changed_reason`` (an older backend) reads as a saved answer, never as the user's act. */
function isOwnerEdit(a: ReadyAnswer): boolean {
  return a.changed_reason === "owner_edit";
}

/** No value to type (a private answer not kept, or blank) and nothing the user saved over it: it waits for the user. */
export function isMissingAnswer(a: ReadyAnswer): boolean {
  return a.changed_to == null && (!!a.redacted || !a.answer.trim());
}

/** One answer: what was typed, its status word and where it came from (lib/answer-status, read
 * from the card's flags like the send check), OK (the user stands behind it) where it is not yet
 * theirs, and Edit (the right answer). Both save the user's word in memory so the assistant learns it. */
function AnswerRow({
  answer: a,
  index,
  card,
  salaryFacts,
  onSaved,
}: {
  salaryFacts?: SalaryFacts;
  answer: ReadyAnswer;
  index: number;
  card: ReadyCardData;
  onSaved: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  // No value to type (a private answer stored hidden, or blank): nothing to OK, only Edit.
  const missing = isMissingAnswer(a);
  // A salary the user already saved in Memory, offered (never saved) until they tap Use this or Edit.
  const suggestion = missing ? suggestSalary(a.question, card.job_country, salaryFacts) : null;
  const label = readyRowLabel(a, card);
  const stub = isDraftStub(a);
  // OK sits where the answer is not yet the user's: a Check row, a private row waiting for their tap,
  // and Memory an assistant saved.
  const checkable = canCheck(a) || label.status !== "ok";
  const id = `${card.application_id}-${index}`;

  async function save(text?: string) {
    setSaving(true);
    try {
      await confirmAnswer(card.application_id, index, card.form_filled_event_id, text);
      toast.success(text === undefined ? "OK. Saved as your answer." : "Saved. Your assistant will use it next time.");
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
      <span className="flex flex-col gap-0.5">
        <span className="text-muted-foreground">{a.question}</span>
        <SourceLine text={label.source} testid={`ready-answer-source-${id}`} />
      </span>
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
      ) : a.changed_to != null && !isOwnerEdit(a) ? (
        // A saved answer, not the user's edit: just the one value that will be typed, never "(blank) -> Yes".
        <span className="flex flex-col gap-0.5 break-words">
          <span data-testid={`ready-answer-changed-${id}`} className="whitespace-pre-wrap font-medium">
            <AnswerText value={a.changed_to} />
          </span>
        </span>
      ) : a.changed_to != null ? (
        <span className="flex flex-col gap-0.5 break-words">
          <span className="text-muted-foreground line-through">{a.answer || "(blank)"}</span>
          <span data-testid={`ready-answer-changed-${id}`} className="whitespace-pre-wrap font-medium">
            <AnswerText value={a.changed_to} />
          </span>
        </span>
      ) : (
        <span className="flex flex-col gap-1 break-words">
          {missing ? (
            <>
              {!a.redacted && (
                <span data-testid={`ready-answer-missing-${id}`} className="font-medium text-danger">
                  Tap Edit to type your answer.
                </span>
              )}
              {suggestion && (
                <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span data-testid={`ready-answer-suggest-${id}`} className="text-xs text-muted-foreground">
                    {`Suggested from your Memory: ${suggestion}`}
                  </span>
                  <button
                    type="button"
                    data-testid={`ready-answer-use-${id}`}
                    disabled={saving}
                    onClick={() => void save(suggestion)}
                    aria-label={`Use this answer for: ${a.question}`}
                    className={cn(tap, "rounded-full border border-info px-2 py-px text-[11px] font-medium text-info hover:bg-info-soft disabled:opacity-50")}
                  >
                    Use this
                  </button>
                </span>
              )}
            </>
          ) : stub ? (
            <DraftMissing id={id} value={a.answer} />
          ) : (
            <AnswerValue id={id} value={a.answer} />
          )}
        </span>
      )}
      <span className="flex flex-wrap items-start gap-1.5 sm:justify-end">
        <StatusWord status={label.status} testid={`ready-answer-status-${id}`} />
        {/* Edit sits on EVERY row (owner 2026-10-26: a private salary row had no way to type it); OK only
            where there is an answer to vouch for. */}
        {!editing && (
          <>
            {checkable && !missing && !a.verified && !stub && a.changed_to == null && (
              <button
                type="button"
                data-testid={`ready-answer-verify-${id}`}
                disabled={saving}
                onClick={() => void save()}
                aria-label={`OK: ${a.question}`}
                className={cn(tap, "inline-flex items-center gap-1 rounded-full border border-info px-2 py-px text-[11px] font-medium text-info hover:bg-info-soft disabled:opacity-50")}
              >
                <Check aria-hidden className="size-3.5" />
                OK
              </button>
            )}
            <button
              type="button"
              data-testid={`ready-answer-edit-${id}`}
              disabled={saving}
              onClick={() => {
                setDraft(a.changed_to ?? (a.answer || suggestion) ?? "");
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
