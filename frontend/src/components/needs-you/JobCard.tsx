"use client";

import Link from "next/link";
import { useState } from "react";
import type { Ask } from "@/lib/api";
import { answerAsk, answerAskQuestions, downloadApplicationArtifact, markCvSeen, markOutreachSent, rememberAnswers, saveAskRowAnswers } from "@/lib/api";
import { effectiveAskBrowser } from "@/lib/browser-contract";
import type { BrowserLabel } from "@/lib/browser-contract";
import { JOB360_BROWSER_LABEL, OWN_BROWSER_LABEL, PACE_TEXT } from "@/lib/browser-labels";
import { BLOCKER_TITLE, isBlocker, needsOwnBrowser, initialRow, isSensitiveQuestion, isStoredSource, mergeQuestions, remindersCovered } from "@/lib/job-cards";
import type { JobCardData, PersonToReach, RowKind } from "@/lib/job-cards";
import { toast } from "@/lib/toast";
import { relativeTime } from "@/lib/utils";
import { Textarea } from "@/components/ui/textarea";
import { FormHandoff } from "@/components/applications/FormHandoff";
import { AskQuestions } from "./AskQuestions";
import { AskText } from "./AskText";
import { BrowserReason } from "./BrowserReason";
import { PersonRow } from "./PersonRow";
import type { RowState } from "./AskQuestions";

// Every string on a card (questions, job fields, names, drafted messages) is written by an
// assistant or a job site: untrusted, rendered only as React text, never as HTML.

const label = "font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint";
const mainBtn =
  "rounded-lg bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50";
const btn =
  "rounded-md border border-border px-2.5 py-1 text-xs font-medium hover:bg-muted/50 disabled:opacity-50";
const picked = "rounded-md bg-primary px-2.5 py-1 text-xs font-semibold text-primary-foreground";

const chipBase = "rounded px-1.5 py-0.5 text-[11px] font-medium";
const warn = "bg-amber-100 text-amber-950 dark:bg-amber-400/20 dark:text-amber-100";

/** Which browser does this action: the backend's label (amber when it is the owner's own browser), else Job360's browser. */
function BrowserChip({ label, testid }: { label: BrowserLabel | null; testid: string }) {
  const own = label?.kind === "own_browser";
  return (
    <span data-testid={testid} className={`${chipBase} ${own ? warn : "bg-muted text-muted-foreground"}`}>
      {label ? label.label : JOB360_BROWSER_LABEL}
    </span>
  );
}

/** The one label a whole card shows: the owner's own browser first, then a human step, then any label. */
function cardBrowser(asks: { ask: Ask; kind: RowKind }[]): BrowserLabel | null {
  const labels = asks.map((a) => effectiveAskBrowser(a.ask, a.kind)).filter((l): l is BrowserLabel => l !== null);
  return labels.find((l) => l.kind === "own_browser") ?? labels.find((l) => l.kind === "human") ?? labels[0] ?? null;
}

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
  const structured = card.asks.filter((a) => a.kind === "structured");
  // Each question is shown once, however many open asks listed it; its answer as it stands and
  // whether the user checked it, per merged row.
  const merged = mergeQuestions(structured.map((s) => s.ask));
  const [rows, setRows] = useState<Record<string, RowState>>({});
  // Rows the owner already saved on this card (an open ask's `answers` hold only the owner's own word: the
  // save is web-session only), so a reload shows them checked, a consent or personal row included.
  const savedRows = new Map<string, string>();
  for (const { ask } of structured)
    for (const k of merged.byAsk.get(ask.id) ?? []) {
      const v = ask.answers?.[k.key];
      if (typeof v === "string" && v.trim()) savedRows.set(k.rowId, v);
    }
  const rowOf = (id: string, q: Parameters<typeof initialRow>[0]): RowState =>
    rows[id] ?? (savedRows.has(id) ? { value: savedRows.get(id)!, ok: true } : initialRow(q));

  const blockers = card.asks.filter((a) => isBlocker(a.kind));
  const questions = card.asks.filter((a) => a.kind === "question" || a.kind === "yes_no");
  const allReminders = card.asks.filter((a) => a.kind === "outreach");
  // A reminder about someone already under People to reach is not a second row (owner 2026-10-24).
  const covered = remindersCovered(card.people, allReminders.map((r) => r.ask));
  const reminders = allReminders.filter((r) => !covered.has(r.ask.id));
  const allPeople = card.people.filter((p) => !hidden.has(p.contact.id));
  const [showAllPeople, setShowAllPeople] = useState(false);
  const rankOf = (p: PersonToReach) => {
    const c = p.contact as unknown as { rank?: unknown; priority?: unknown };
    const v = typeof c.rank === "number" ? c.rank : typeof c.priority === "number" ? c.priority : null;
    return v;
  };
  // Best first: an explicit numeric rank/priority (ascending) wins; otherwise the order given.
  const ordered = allPeople.some((p) => rankOf(p) !== null)
    ? [...allPeople].sort((a, b) => (rankOf(a) ?? Infinity) - (rankOf(b) ?? Infinity))
    : allPeople;
  const people = showAllPeople ? ordered : ordered.slice(0, 3);
  const morePeople = ordered.length - people.length;

  /** After `contactId` is sent or skipped: answer each covered reminder whose people are all done. */
  async function resolveReminders(contactId: number, answer: string) {
    const done = new Set([...hidden, contactId]);
    for (const [askId, ids] of covered) {
      if (ids.includes(contactId) && ids.every((id) => done.has(id))) await answerAsk(askId, answer);
    }
  }
  const rowCount = merged.rows.length;
  const allChecked = merged.rows.every((m) => rowOf(m.id, m.q).ok && rowOf(m.id, m.q).value.trim());
  const allAnswered = allChecked && questions.every((q) => (answers[q.ask.id] ?? "").trim().length > 0);
  const hasAnswers = questions.length > 0 || rowCount > 0;
  const canApprove = !busy && allAnswered && (hasAnswers || card.cv !== null);
  // Rows "Approve all pre-filled" can check: an answer is there, and it is not a consent or personal one.
  const prefillable = merged.rows.filter((m) => {
    const r = rowOf(m.id, m.q);
    return !r.ok && r.value.trim() && !isSensitiveQuestion(m.q);
  });
  const prefillMode = merged.rows.some((m) => !rowOf(m.id, m.q).ok || !rowOf(m.id, m.q).value.trim());

  /** Owner bug 2026-10-26: an edit saves at once, one row on its own (a row still waiting never holds
   *  it back): onto every open ask that lists the row, and as a remembered answer unless it is the stored
   *  fact unchanged. The row shows checked first and goes back if the save fails. */
  async function persistRow(id: string, value: string) {
    const m = merged.rows.find((r) => r.id === id);
    const text = value.trim();
    if (!m || !text) return;
    for (const { ask } of structured) {
      const keys = (merged.byAsk.get(ask.id) ?? []).filter((k) => k.rowId === id).map((k) => k.key);
      if (keys.length) await saveAskRowAnswers(ask.id, Object.fromEntries(keys.map((k) => [k, text])));
    }
    if (!(isStoredSource(m.q.source) && text === m.q.answer.trim()))
      await rememberAnswers([{ topic: m.q.key || m.id, question: m.q.question, answer: text }]);
  }

  function saveRows(entries: [string, string][]) {
    const before = Object.fromEntries(entries.map(([id]) => [id, rows[id]]));
    const now = Object.fromEntries(entries.map(([id, value]) => [id, { value: value.trim(), ok: true }]));
    setRows((prev) => ({ ...prev, ...now }));
    setBusy(true);
    void (async () => {
      try {
        for (const [id] of entries) await persistRow(id, now[id].value);
        await onChanged();
      } catch (err) {
        setRows((prev) => {
          const next = { ...prev };
          for (const [id] of entries) {
            if (before[id]) next[id] = before[id];
            else delete next[id];
          }
          return next;
        });
        toast.apiError(err, "Couldn't save that answer");
      } finally {
        setBusy(false);
      }
    })();
  }

  const saveRow = (id: string, value: string) => saveRows([[id, value]]);
  const approvePrefilled = () => saveRows(prefillable.map((m) => [m.id, rowOf(m.id, m.q).value]));

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
      const finalOf = (id: string) => {
        const m = merged.rows.find((r) => r.id === id)!;
        return rowOf(id, m.q).value.trim();
      };
      // Every underlying ask is answered with its own keys, even one whose rows were all merged away.
      for (const { ask } of structured) {
        const given = Object.fromEntries((merged.byAsk.get(ask.id) ?? []).map((k) => [k.key, finalOf(k.rowId)]));
        await answerAskQuestions(ask.id, given);
      }
      // Every confirmed answer becomes a permanent fact, except one already stored and unchanged.
      if (rowCount > 0) await rememberAnswers(
        merged.rows
          .filter((m) => !(isStoredSource(m.q.source) && finalOf(m.id) === m.q.answer.trim()))
          .map((m) => ({ topic: m.q.key || m.id, question: m.q.question, answer: finalOf(m.id) })),
      );
      if (card.cv) await markCvSeen(card.applicationId, card.cv.artifactId);
    }, "Couldn't save everything");

  const skipJob = () =>
    run(async () => {
      for (const a of card.asks) await answerAsk(a.ask.id, SKIP_JOB);
    }, "Couldn't skip this job");

  const setAnswer = (id: number, v: string) => setAnswers((prev) => ({ ...prev, [id]: v }));

  // Every row that asks the owner to act carries a link: the form, else the application page.
  const rowLink = (id: number) =>
    card.formUrl ? (
      <a href={card.formUrl} target="_blank" rel="noopener noreferrer" className={btn} data-testid={`row-link-${id}`}>
        Open the form ↗
      </a>
    ) : (
      <Link href={`/applications/${card.applicationId}`} className={btn} data-testid={`row-link-${id}`}>
        Open the application
      </Link>
    );

  const blockerRow = ({ ask, kind }: { ask: Ask; kind: RowKind }) => (
    <div key={ask.id} className="flex flex-col gap-2 rounded-lg bg-muted/30 p-3" data-testid={`blocker-${ask.id}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="text-sm font-medium">{isBlocker(kind) ? BLOCKER_TITLE[kind] : ""}</p>
        <div className="flex flex-col items-end gap-0.5 text-right">
          <BrowserChip label={effectiveAskBrowser(ask, kind)} testid={`browser-chip-${ask.id}`} />
          {effectiveAskBrowser(ask, kind) && <BrowserReason label={effectiveAskBrowser(ask, kind)!} testid={`browser-reason-${ask.id}`} />}
        </div>
      </div>
      <AskText id={ask.id} question={ask.question} headClass="text-xs text-muted-foreground" />
      {(kind === "captcha" || kind === "login") && (
        // Job360's browser holds the filled form: finish it there. The employer page is an EMPTY form.
        <FormHandoff applicationId={card.applicationId} finishHere={ask.finish_here === true} canRefill={ask.can_refill}
          heldSeconds={ask.held_seconds} formUrl={card.formUrl} finishTestId={`finish-here-${ask.id}`} />
      )}
      <div className="flex flex-wrap gap-2">
        {(kind !== "captcha" && kind !== "login") || !card.formUrl ? rowLink(ask.id) : null}
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
  );

  return (
    <li data-testid={`job-card-${card.applicationId}`} className="flex flex-col gap-4 rounded-xl border border-border bg-card p-5 shadow-card">
      {showHeader && (
      <div className="flex flex-col gap-0.5">
        <Link href={`/applications/${card.applicationId}`} className="font-heading text-xl leading-snug underline-offset-2 hover:underline">
          {card.company || "Unknown company"}
        </Link>
        <p className="text-sm">
          <Link href={`/applications/${card.applicationId}`} data-testid={`title-link-${card.applicationId}`}
            className="underline-offset-2 hover:underline">
            {card.role || "Untitled role"}
          </Link>
        </p>
        <p className="text-xs text-muted-foreground">
          since {relativeTime(card.oldestAskAt)}
        </p>
      </div>
      )}

      <section className="flex flex-col gap-3" data-testid={`section-job-${card.applicationId}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-heading text-base">Job</h2>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted-foreground" data-testid={`apply-status-${card.applicationId}`}>
            {card.formFilled ? "Form filled, waiting for you" : "Waiting for you"}
          </span>
          {card.formUrl && (
            <a href={card.formUrl} target="_blank" rel="noopener noreferrer" className={btn} data-testid={`section-form-link-${card.applicationId}`}>
              Open the form ↗
            </a>
          )}
        </div>
      </div>

      {blockers.length > 0 && (
        <section className="flex flex-col gap-2" data-testid="only-you">
          <h3 className={label}>Only you can do this</h3>
          {blockers.filter((b) => !needsOwnBrowser(b.ask)).map(blockerRow)}
          {blockers.some((b) => needsOwnBrowser(b.ask)) && (
            <div className="flex flex-col gap-2" data-testid={`own-chrome-${card.applicationId}`}>
              <h4 className="text-xs font-semibold">{OWN_BROWSER_LABEL}</h4>
              {blockers.filter((b) => needsOwnBrowser(b.ask)).map(blockerRow)}
            </div>
          )}
        </section>
      )}

      {hasAnswers && (
        <section className="flex flex-col gap-2" data-testid="form-answers">
          <h3 className={label}>Form answers · {questions.length + rowCount}</h3>
          {rowCount > 0 && (
            <AskQuestions items={merged.rows} busy={busy}
              rows={Object.fromEntries(merged.rows.map((m) => [m.id, rowOf(m.id, m.q)]))}
              onChange={(id, row) => setRows((prev) => ({ ...prev, [id]: row }))} onSave={saveRow} />
          )}
          {questions.map(({ ask, kind }) => (
            <div key={ask.id} className="flex flex-col gap-2 rounded-lg bg-muted/30 p-3" data-testid={`question-${ask.id}`}>
              <AskText id={ask.id} question={ask.question} context={ask.context} headClass="text-sm font-medium" />
              <div>{rowLink(ask.id)}</div>
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

      </section>

      {(people.length > 0 || reminders.length > 0) && (
        <section className="flex flex-col gap-2" data-testid={`section-outreach-${card.applicationId}`}>
          <h2 className="font-heading text-base">Outreach</h2>
          <p className="text-xs text-muted-foreground" data-testid={`outreach-pace-${card.applicationId}`}>{PACE_TEXT}</p>
          <div className="flex flex-col gap-2" data-testid="people-to-reach">
          {people.map((p) => (
            <PersonRow key={p.contact.id} person={p} busy={busy} onChanged={onChanged}
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
          {morePeople > 0 && (
            <button type="button" className={btn} data-testid={`people-more-${card.applicationId}`}
              onClick={() => setShowAllPeople(true)}>
              Show {morePeople} more
            </button>
          )}
          {reminders.map(({ ask }) => (
            <div key={ask.id} className="flex flex-col gap-2 rounded-lg bg-muted/30 p-3" data-testid={`reminder-${ask.id}`}>
              <AskText id={ask.id} question={ask.question} headClass="text-sm" />
              <div className="flex flex-wrap gap-2">
                {rowLink(ask.id)}
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
          </div>
        </section>
      )}

      <div className="flex flex-wrap items-center gap-3 border-t border-border pt-3">
        {card.asks.length > 0 && (() => {
          const cb = cardBrowser(card.asks);
          return (
            <span className="flex flex-col gap-0.5">
              <BrowserChip label={cb} testid={`browser-chip-card-${card.applicationId}`} />
              {cb && <BrowserReason label={cb} testid={`browser-reason-card-${card.applicationId}`} />}
            </span>
          );
        })()}
        {prefillMode ? (
          <button type="button" className={mainBtn} disabled={busy || prefillable.length === 0}
            data-testid={`approve-prefilled-${card.applicationId}`} onClick={approvePrefilled}>
            Approve all pre-filled
          </button>
        ) : (
          (hasAnswers || card.cv) && (
            <button type="button" className={mainBtn} disabled={!canApprove} data-testid={`approve-all-${card.applicationId}`}
              onClick={() => void approveAll()}>
              {busy ? "Saving…" : "Approve everything and continue"}
            </button>
          )
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
            <button type="button" className="rounded-md px-2.5 py-1 text-xs font-medium text-muted-foreground hover:bg-muted/50" data-testid={`skip-job-${card.applicationId}`} onClick={() => setConfirmSkip(true)}>
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
