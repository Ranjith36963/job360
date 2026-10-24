"use client";

import Link from "next/link";
import { useState } from "react";
import { answerAsk, updateContact, answerAskQuestions, downloadApplicationArtifact, markCvSeen, markOutreachSent, rememberAnswers } from "@/lib/api";
import { closedSetLabel } from "@/lib/closed-sets";
import { BLOCKER_TITLE, LINKEDIN_STATE_LABEL, NOTE_MAX_CHARS, isBlocker, linkedinState, remindersCovered } from "@/lib/job-cards";
import type { JobCardData, PersonToReach } from "@/lib/job-cards";
import type { Ask } from "@/lib/api";
import { toast } from "@/lib/toast";
import { relativeTime } from "@/lib/utils";
import { Textarea } from "@/components/ui/textarea";
import { FinishInBrowser } from "@/components/applications/FinishInBrowser";
import { AskQuestions, initialRows } from "./AskQuestions";
import type { RowState } from "./AskQuestions";

// Every string on a card (questions, job fields, names, drafted messages) is written by an
// assistant or a job site: untrusted, rendered only as React text, never as HTML.

const label = "font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint";
const mainBtn =
  "rounded-lg bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50";
const btn =
  "rounded-md border border-border px-2.5 py-1 text-xs font-medium hover:bg-muted/50 disabled:opacity-50";
const picked = "rounded-md bg-primary px-2.5 py-1 text-xs font-semibold text-primary-foreground";

const DID_IT = "I did it myself on the form.";
const SKIP_JOB = "Skip this job: do not apply.";

/** `showHeader` false: the application page, whose own title already names the job. */
export function JobCard({
  card,
  onChanged,
  showHeader = true,
}: {
  card: JobCardData;
  onChanged: () => Promise<void>;
  showHeader?: boolean;
}) {
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [hidden, setHidden] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [confirmSkip, setConfirmSkip] = useState(false);
  // A CAPTCHA on a form filled in Job360's own browser: the user can finish it right here.
  const [finishing, setFinishing] = useState<number | null>(null);
  const structured = card.asks.filter((a) => a.kind === "structured");
  // Each listed question's answer as it stands and whether the user checked it, per ask.
  const [rows, setRows] = useState<Record<number, Record<string, RowState>>>({});
  const rowsFor = (ask: Ask) => rows[ask.id] ?? initialRows(ask.questions ?? []);
  const [remember, setRemember] = useState(false);

  const blockers = card.asks.filter((a) => isBlocker(a.kind));
  const questions = card.asks.filter((a) => a.kind === "question" || a.kind === "yes_no");
  const allReminders = card.asks.filter((a) => a.kind === "outreach");
  // A reminder about someone already under People to reach is not a second row (owner 2026-10-24).
  const covered = remindersCovered(card.people, allReminders.map((r) => r.ask));
  const reminders = allReminders.filter((r) => !covered.has(r.ask.id));
  const people = card.people.filter((p) => !hidden.has(p.contact.id));

  /** After `contactId` is sent or skipped: answer each covered reminder whose people are all done. */
  async function resolveReminders(contactId: number, answer: string) {
    const done = new Set([...hidden, contactId]);
    for (const [askId, ids] of covered) {
      if (ids.includes(contactId) && ids.every((id) => done.has(id))) await answerAsk(askId, answer);
    }
  }
  const rowCount = structured.reduce((n, { ask }) => n + (ask.questions?.length ?? 0), 0);
  const allChecked = structured.every(({ ask }) =>
    (ask.questions ?? []).every((q) => rowsFor(ask)[q.key]?.ok && rowsFor(ask)[q.key].value.trim()),
  );
  const allAnswered = allChecked && questions.every((q) => (answers[q.ask.id] ?? "").trim().length > 0);
  const hasAnswers = questions.length > 0 || rowCount > 0;
  const canApprove = !busy && allAnswered && (hasAnswers || card.cv !== null);

  async function run(fn: () => Promise<unknown>, failMsg: string) {
    setBusy(true);
    try {
      await fn();
      await onChanged();
    } catch (err) {
      toast.apiError(err, failMsg);
    } finally {
      setBusy(false);
    }
  }

  const approveAll = () =>
    run(async () => {
      for (const q of questions) await answerAsk(q.ask.id, answers[q.ask.id].trim());
      for (const { ask } of structured) {
        const qs = ask.questions ?? [];
        const given = Object.fromEntries(qs.map((q) => [q.key, rowsFor(ask)[q.key].value.trim()]));
        await answerAskQuestions(ask.id, given);
        if (remember) await rememberAnswers(qs.map((q) => ({ topic: q.key, question: q.question, answer: given[q.key] })));
      }
      if (card.cv) await markCvSeen(card.applicationId, card.cv.artifactId);
    }, "Couldn't save everything");

  const skipJob = () =>
    run(async () => {
      for (const a of card.asks) await answerAsk(a.ask.id, SKIP_JOB);
    }, "Couldn't skip this job");

  const setAnswer = (id: number, v: string) => setAnswers((prev) => ({ ...prev, [id]: v }));

  return (
    <li data-testid={`job-card-${card.applicationId}`} className="flex flex-col gap-4 rounded-xl border border-border bg-card p-5 shadow-card">
      {showHeader && (
      <div className="flex flex-col gap-0.5">
        <Link href={`/applications/${card.applicationId}`} className="font-heading text-xl leading-snug underline-offset-2 hover:underline">
          {card.company || "Unknown company"}
        </Link>
        <p className="text-sm">{card.role || "Untitled role"}</p>
        <p className="text-xs text-muted-foreground">
          {card.formFilled ? "Form filled, waiting for you" : "Waiting for you"} · since {relativeTime(card.oldestAskAt)}
        </p>
      </div>
      )}

      {blockers.length > 0 && (
        <section className="flex flex-col gap-2" data-testid="only-you">
          <h3 className={label}>Only you can do this</h3>
          {blockers.map(({ ask, kind }) => (
            <div key={ask.id} className="flex flex-col gap-2 rounded-lg bg-muted/30 p-3" data-testid={`blocker-${ask.id}`}>
              <p className="text-sm font-medium">{isBlocker(kind) ? BLOCKER_TITLE[kind] : ""}</p>
              <p className="whitespace-pre-wrap text-xs text-muted-foreground">{ask.question}</p>
              {finishing === ask.id && <FinishInBrowser onDone={() => setFinishing(null)} />}
              <div className="flex flex-wrap gap-2">
                {ask.finish_here && finishing !== ask.id && (
                  <button type="button" className={btn} data-testid={`finish-here-${ask.id}`} onClick={() => setFinishing(ask.id)}>
                    Finish it here
                  </button>
                )}
                {card.formUrl && (
                  <a href={card.formUrl} target="_blank" rel="noopener noreferrer" className={btn}>
                    Open the form ↗
                  </a>
                )}
                {kind === "upload" && card.latestCv && (
                  <button type="button" className={btn} onClick={() => void downloadCv(card)}>
                    Download CV
                  </button>
                )}
                <button type="button" className={btn} disabled={busy} data-testid={`did-it-${ask.id}`}
                  onClick={() => void run(() => answerAsk(ask.id, DID_IT), "Couldn't save that")}>
                  I did it
                </button>
              </div>
            </div>
          ))}
        </section>
      )}

      {hasAnswers && (
        <section className="flex flex-col gap-2" data-testid="form-answers">
          <h3 className={label}>Form answers · {questions.length + rowCount}</h3>
          {structured.map(({ ask }) => (
            <AskQuestions key={ask.id} askId={ask.id} questions={ask.questions ?? []} rows={rowsFor(ask)} busy={busy}
              onChange={(key, row) => setRows((prev) => ({ ...prev, [ask.id]: { ...rowsFor(ask), ...prev[ask.id], [key]: row } }))} />
          ))}
          {rowCount > 0 && (
            <label className="flex items-center gap-2 text-xs">
              <input type="checkbox" checked={remember} disabled={busy} data-testid={`remember-${card.applicationId}`}
                onChange={(e) => setRemember(e.target.checked)} />
              Remember these answers for every form (saved to your profile)
            </label>
          )}
          {questions.map(({ ask, kind }) => (
            <div key={ask.id} className="flex flex-col gap-2 rounded-lg bg-muted/30 p-3" data-testid={`question-${ask.id}`}>
              <p className="whitespace-pre-wrap text-sm font-medium">{ask.question}</p>
              {ask.context && <p className="whitespace-pre-wrap text-xs text-muted-foreground">{ask.context}</p>}
              {kind === "yes_no" ? (
                <div className="flex gap-2">
                  {["Yes", "No"].map((v) => (
                    <button key={v} type="button" disabled={busy} className={answers[ask.id] === v ? picked : btn}
                      aria-pressed={answers[ask.id] === v} onClick={() => setAnswer(ask.id, v)}>
                      {v}
                    </button>
                  ))}
                </div>
              ) : (
                <Textarea aria-label="Your answer" value={answers[ask.id] ?? ""} disabled={busy}
                  data-testid={`answer-${ask.id}`} onChange={(e) => setAnswer(ask.id, e.target.value)} />
              )}
            </div>
          ))}
        </section>
      )}

      {card.cv && (
        <section className="flex flex-wrap items-center justify-between gap-2" data-testid="cv-to-check">
          <div className="flex flex-col gap-0.5">
            <h3 className={label}>CV to check</h3>
            <p className="text-sm">CV version {card.cv.version}, not checked yet</p>
          </div>
          <div className="flex gap-2">
            <Link href={`/applications/${card.applicationId}`} className={btn}>
              View CV
            </Link>
            <button type="button" className={btn} disabled={busy} data-testid={`cv-ok-${card.applicationId}`}
              onClick={() => void run(() => markCvSeen(card.applicationId, card.cv!.artifactId), "Couldn't save that you checked this CV")}>
              Approve
            </button>
          </div>
        </section>
      )}

      {(people.length > 0 || reminders.length > 0) && (
        <section className="flex flex-col gap-2" data-testid="people-to-reach">
          <h3 className={label}>People to reach</h3>
          {people.map((p) => (
            <PersonRow key={p.contact.id} person={p} busy={busy}
              onState={(state) => void run(() => updateContact(p.contact.id, { linkedin_state: state }), "Couldn't save that")}
              onSent={() => void run(async () => {
                await markOutreachSent(p.contact.id, "linkedin");
                setHidden((prev) => new Set(prev).add(p.contact.id)); // sent: handled, before the reload
                await resolveReminders(p.contact.id, "I sent it myself.");
              }, "Couldn't save that you sent it")}
              onSkip={() => {
                setHidden((prev) => new Set(prev).add(p.contact.id));
                void run(() => resolveReminders(p.contact.id, "Skip: do not send it."), "Couldn't save that");
              }} />
          ))}
          {reminders.map(({ ask }) => (
            <div key={ask.id} className="flex flex-col gap-2 rounded-lg bg-muted/30 p-3" data-testid={`reminder-${ask.id}`}>
              <p className="whitespace-pre-wrap text-sm">{ask.question}</p>
              <div className="flex gap-2">
                <button type="button" className={btn} disabled={busy}
                  onClick={() => void run(() => answerAsk(ask.id, "I sent it myself."), "Couldn't save that")}>
                  I sent it
                </button>
                <button type="button" className={btn} disabled={busy}
                  onClick={() => void run(() => answerAsk(ask.id, "Skip: do not send it."), "Couldn't save that")}>
                  Skip
                </button>
              </div>
            </div>
          ))}
        </section>
      )}

      <div className="flex flex-wrap items-center gap-3 border-t border-border pt-3">
        {(hasAnswers || card.cv) && (
          <button type="button" className={mainBtn} disabled={!canApprove} data-testid={`approve-all-${card.applicationId}`}
            onClick={() => void approveAll()}>
            {busy ? "Saving…" : "Approve everything and continue"}
          </button>
        )}
        {card.asks.length > 0 &&
          (confirmSkip ? (
            <span className="flex flex-wrap items-center gap-2 text-xs">
              Skip this job? Your assistant will not apply.
              <button type="button" className={btn} disabled={busy} data-testid={`skip-confirm-${card.applicationId}`} onClick={() => void skipJob()}>
                Yes, skip it
              </button>
              <button type="button" className={btn} disabled={busy} onClick={() => setConfirmSkip(false)}>
                Cancel
              </button>
            </span>
          ) : (
            <button type="button" className={btn} data-testid={`skip-job-${card.applicationId}`} onClick={() => setConfirmSkip(true)}>
              Skip this job
            </button>
          ))}
      </div>
    </li>
  );
}

async function downloadCv(card: JobCardData) {
  if (!card.latestCv) return;
  try {
    await downloadApplicationArtifact(card.applicationId, card.latestCv.artifactId, "pdf", `CV_v${card.latestCv.version}`);
  } catch (err) {
    toast.apiError(err, "Couldn't download this CV");
  }
}

function PersonRow({
  person,
  busy,
  onSent,
  onSkip,
  onState,
}: {
  person: PersonToReach;
  busy: boolean;
  onSent: () => void;
  onSkip: () => void;
  onState: (state: "connected" | "not_connected") => void;
}) {
  const [copied, setCopied] = useState(false);
  const c = person.contact;
  const where = c.found_via ? `Found via ${closedSetLabel(c.found_via)}` : "";
  const state = linkedinState(c.linkedin_state);
  const chars = person.message.length;
  return (
    <div className="flex flex-col gap-2 rounded-lg bg-muted/30 p-3" data-testid={`person-${c.id}`}>
      <p className="text-sm font-medium">
        {c.name}
        {c.role && <span className="font-normal text-muted-foreground"> · {c.role}</span>}
      </p>
      {where && <p className="text-xs text-muted-foreground">{where}</p>}
      <p className="whitespace-pre-wrap rounded-md border border-border bg-card p-2 text-sm">{person.message}</p>
      <div className="flex flex-wrap items-center gap-2 text-xs" data-testid={`li-state-${c.id}`}>
        <span className="text-muted-foreground">{LINKEDIN_STATE_LABEL[state]}</span>
        {state === "not_connected" && (
          <span data-testid={`note-count-${c.id}`} className={chars > NOTE_MAX_CHARS ? "font-medium text-destructive" : "text-muted-foreground"}>
            {chars}/{NOTE_MAX_CHARS} characters
          </span>
        )}
        {(["connected", "not_connected"] as const).map((s) => (
          <button key={s} type="button" disabled={busy} aria-pressed={state === s} data-testid={`li-${s}-${c.id}`}
            className={state === s ? picked : btn} onClick={() => onState(s)}>
            {s === "connected" ? "We're connected" : "Not connected yet"}
          </button>
        ))}
      </div>
      {state !== "pending" && (
      <div className="flex flex-wrap gap-2">
        <button type="button" className={btn}
          onClick={() => navigator.clipboard.writeText(person.message).then(() => setCopied(true), () => toast.error("Couldn't copy"))}>
          {copied ? "Copied" : "Copy message"}
        </button>
        <a href={person.linkedinUrl} target="_blank" rel="noopener noreferrer" className={btn}>
          Open LinkedIn profile ↗
        </a>
        <button type="button" className={btn} disabled={busy} data-testid={`sent-${c.id}`} onClick={onSent}>
          I sent it
        </button>
        <button type="button" className={btn} onClick={onSkip}>
          Skip
        </button>
      </div>
      )}
    </div>
  );
}
