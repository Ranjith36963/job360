"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { ASKS_CHANGED_EVENT, answerAsk, confirmAsk, getAssistantSettings, listAsks, withdrawAsk } from "@/lib/api";
import type { Ask } from "@/lib/api";
import { LIVE_SUBMITTED_EVENT } from "@/lib/live-submitted";
import { ChevronDown, ChevronRight } from "lucide-react";
import { OtherList } from "./OtherList";
import type { WaitingRequest } from "./SettingRequestCard";
import { settingScore } from "@/lib/assistant-tab";
import { askerName, whoLabel } from "@/lib/event-labels";
import { relativeTime } from "@/lib/utils";
import { Textarea } from "@/components/ui/textarea";
import { AnswerText } from "@/components/AnswerText";
import { AskText } from "./AskText";
import { JobCards } from "./JobCards";
import { useLiveAsks, useLiveRefresh, useLiveRequests } from "./useLiveLists";
import type { JobCardsReport } from "./JobCards";
import { groupAsks } from "@/lib/job-cards";
import { RunChromeTasks } from "./RunChromeTasks";
import { OwnBrowserQueueCard } from "./OwnBrowserQueueCard";
import { getOwnBrowserQueue, type OwnBrowserQueue } from "@/lib/browser-contract";
import { FormHandoff } from "@/components/applications/FormHandoff";
import { countNeedsYou, listAllOpenAsks, readSort, saveSort } from "@/lib/needs-you-items";
import type { Chip, SortKey } from "@/lib/needs-you-items";
import { NeedsYouControls, readShow, writeShow } from "./NeedsYouControls";
// Every string on an ask (question, context, answer, job fields) is written by
// the user's assistant or by the user — untrusted. It is only ever rendered as
// a React text node, never as HTML or markdown.

type Pending = "answer" | "withdraw" | null;

// One page of the answered history; the route pages, it never caps.
const PAGE = 50;

const confirmBtn =
  "rounded-md bg-primary px-2.5 py-1 text-xs font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50";
const cancelBtn =
  "rounded-md border border-border px-2.5 py-1 text-xs font-medium text-muted-foreground hover:text-foreground disabled:opacity-50";
const linkBtn =
  "text-xs font-medium text-muted-foreground underline-offset-2 hover:text-foreground hover:underline";
const confirmBox =
  "flex flex-wrap items-center gap-2 rounded-lg border border-border bg-muted/30 px-3 py-2 text-xs";

export function AskCard({
  ask,
  mode,
  onChanged,
}: {
  ask: Ask;
  mode: "open" | "answered";
  onChanged: () => Promise<void>;
}) {
  const [editing, setEditing] = useState(mode === "open");
  const [draft, setDraft] = useState(mode === "open" ? "" : (ask.answer ?? ""));
  const [pending, setPending] = useState<Pending>(null);
  const [busy, setBusy] = useState(false);
  // "Finish it here": the CAPTCHA in Job360's browser, solved on this card (owner, 2026-10-24).
  const [finish, setFinish] = useState<"off" | "done">("off");

  async function run(fn: () => Promise<unknown>, failMsg: string, onDone?: () => void) {
    setBusy(true);
    try {
      await fn();
      setPending(null);
      onDone?.();
      await onChanged();
    } catch {
      toast.error(failMsg);
    } finally {
      setBusy(false);
    }
  }

  const job = [ask.job_title, ask.job_company].filter(Boolean).join(" · ");
  const canSave = draft.trim().length > 0 && !busy;

  return (
    <li
      data-testid={`ask-${ask.id}`}
      className="flex flex-col gap-3 rounded-xl border border-border bg-card p-5 shadow-card"
    >
      <div className="flex flex-col gap-1.5">
        <AskText id={ask.id} question={ask.question} context={ask.context}
          headClass="font-heading text-xl leading-snug" restClass="whitespace-pre-wrap text-sm text-muted-foreground" />
        <p className="text-[12.5px] text-muted-foreground">
          asked by {askerName(ask.asked_by)} · <span className="font-mono">{relativeTime(ask.asked_at)}</span>
          {job && " · "}
          {job &&
            (ask.application_id != null ? (
              <Link
                href={`/applications/${ask.application_id}`}
                className="underline-offset-2 hover:underline"
              >
                {job}
              </Link>
            ) : (
              <span>{job}</span>
            ))}
        </p>
      </div>

      {mode === "open" && ask.application_id != null && finish !== "done" && (ask.finish_here || ask.can_refill === true) && (
        <FormHandoff applicationId={ask.application_id} finishHere={ask.finish_here === true} canRefill={ask.can_refill}
          heldSeconds={ask.held_seconds} finishTestId="ask-finish-here" onFinished={() => setFinish("done")} />
      )}
      {finish === "done" && ask.application_id != null && (
        <p data-testid="ask-finish-done" className="text-xs text-muted-foreground">
          Did it go through?{" "}
          <Link href={`/applications/${ask.application_id}`} className="underline underline-offset-2">
            Press I submitted it on the application
          </Link>
          , or tell your assistant.
        </p>
      )}

      {mode === "answered" && !editing && (
        <div className="flex flex-col gap-1 rounded-lg bg-muted/30 p-3">
          {ask.relayed_from_chat && ask.user_words ? (
            <p className="whitespace-pre-wrap text-sm" data-testid="ask-relayed-from-chat">
              You said (via {whoLabel(ask.answered_by ?? "").name}): {ask.user_words}
            </p>
          ) : null}
          <p className="whitespace-pre-wrap text-sm"><AnswerText value={ask.answer} /></p>
          <p className="text-xs text-muted-foreground" data-testid="ask-provenance">
            {ask.answered_by_user
              ? "You answered on Job360"
              : "Your assistant recorded your answer from chat"}
          </p>
          {ask.relayed_from_chat &&
            (ask.confirmed_at ? (
              <p className="text-xs font-medium" data-testid="ask-confirmed">
                You confirmed
              </p>
            ) : (
              <div>
                <button
                  type="button"
                  data-testid="confirm-relayed"
                  disabled={busy}
                  onClick={() => void run(() => confirmAsk(ask.id), "Could not confirm this answer.")}
                  className={confirmBtn}
                >
                  {busy ? "Confirming…" : "Confirm"}
                </button>
              </div>
            ))}
          <div>
            <button
              type="button"
              data-testid="ask-change"
              onClick={() => setEditing(true)}
              className={linkBtn}
            >
              Change answer
            </button>
          </div>
        </div>
      )}

      {editing && (
        <div className="flex flex-col gap-2">
          <Textarea
            aria-label="Your answer"
            data-testid={`ask-input-${ask.id}`}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            disabled={busy}
          />
          {pending === "answer" ? (
            <div className={confirmBox}>
              <span>Save this answer? Your assistants will use it.</span>
              <button
                type="button"
                data-testid="ask-answer-confirm"
                disabled={busy}
                onClick={() =>
                  void run(
                    () => answerAsk(ask.id, draft.trim()),
                    "Could not save your answer.",
                    // A changed answer goes back to the read-only view; an open
                    // card moves to the Answered list and remounts there.
                    () => {
                      if (mode === "answered") setEditing(false);
                    },
                  )
                }
                className={confirmBtn}
              >
                {busy ? "Saving…" : "Confirm"}
              </button>
              <button
                type="button"
                data-testid="ask-answer-cancel"
                disabled={busy}
                onClick={() => setPending(null)}
                className={cancelBtn}
              >
                Cancel
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-3">
              <button
                type="button"
                data-testid="ask-save"
                disabled={!canSave}
                onClick={() => setPending("answer")}
                className="rounded-lg bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
              >
                Save answer
              </button>
              {mode === "answered" && (
                <button
                  type="button"
                  onClick={() => {
                    setEditing(false);
                    setDraft(ask.answer ?? "");
                  }}
                  className={linkBtn}
                >
                  Cancel
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {mode === "open" &&
        (pending === "withdraw" ? (
          <div className={confirmBox}>
            <span>Withdraw this question? Your assistant will stop waiting on it.</span>
            <button
              type="button"
              data-testid="ask-withdraw-confirm"
              disabled={busy}
              onClick={() =>
                void run(() => withdrawAsk(ask.id), "Could not withdraw this question.")
              }
              className={confirmBtn}
            >
              {busy ? "Withdrawing…" : "Confirm"}
            </button>
            <button
              type="button"
              data-testid="ask-withdraw-cancel"
              disabled={busy}
              onClick={() => setPending(null)}
              className={cancelBtn}
            >
              Cancel
            </button>
          </div>
        ) : (
          <div>
            <button
              type="button"
              data-testid="ask-withdraw"
              onClick={() => setPending("withdraw")}
              className={linkBtn}
            >
              Withdraw
            </button>
          </div>
        ))}
    </li>
  );
}

/** An answered ask as one line; "Change" opens the full card to edit or confirm it. */
function AnsweredRow({ ask, onChanged }: { ask: Ask; onChanged: () => Promise<void> }) {
  const [openRow, setOpenRow] = useState(false);
  if (openRow) return <AskCard ask={ask} mode="answered" onChanged={onChanged} />;
  const job = [ask.job_company, ask.job_title].filter(Boolean).join(" · ");
  return (
    <li data-testid={`answered-${ask.id}`} className="flex items-center justify-between gap-3 rounded-lg border border-border px-3 py-2 text-sm">
      <span className="min-w-0 truncate">
        {job && <span className="text-muted-foreground">{job}: </span>}
        {ask.question} <span className="text-muted-foreground">→ <AnswerText value={ask.answer} /></span>
      </span>
      <button type="button" data-testid={`answered-change-${ask.id}`} onClick={() => setOpenRow(true)} className={linkBtn}>
        Change
      </button>
    </li>
  );
}

/** The requests waiting for the user's OK. Best-effort: a failed read just
 *  shows none, it never blocks the questions. */
async function waitingRequests(): Promise<WaitingRequest[]> {
  try {
    const view = await getAssistantSettings();
    const line = settingScore(view.apply_min_score);
    return view.waiting.map((r) => ({ ...r, current_score_line: line, grant_queue: view.auto_grant_scope?.queue ?? null }));
  } catch {
    return [];
  }
}

/** Every page of open asks, the first page of answered ones and the waiting setting changes.
 *  The job cards load themselves, lazily. */
async function fetchAll() {
  const [o, a, w] = await Promise.all([listAllOpenAsks(), listAsks("answered"), waitingRequests()]);
  return [o, a, w] as const;
}

function announce(openCount: number) {
  window.dispatchEvent(new CustomEvent(ASKS_CHANGED_EVENT, { detail: openCount }));
}

export function NeedsYou() {
  const [open, setOpen] = useState<Ask[] | null>(null);
  // What the job list reports: its loaded cards, the asks it could not place, how many jobs it lists.
  const [jobs, setJobs] = useState<JobCardsReport>({ cards: [], failed: [], total: 0, settled: false, readyTotal: null });
  // Setting changes an assistant asked for, waiting for the user's OK (S2).
  const [waiting, setWaiting] = useState<WaitingRequest[]>([]);
  const [answered, setAnswered] = useState<Ask[]>([]);
  const [moreAnswered, setMoreAnswered] = useState(false);
  // Answered is folded until the user opens it (owner plan 3b).
  const [showAnswered, setShowAnswered] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Outcome suggestions and unmatched mail, as the Other list loaded them (for the one count).
  const [otherLoaded, setOtherLoaded] = useState({ outcomes: 0, mail: 0 });
  // The order and the chip: the order is kept per browser, the chip in the link (?show=).
  const [sort, setSort] = useState<SortKey>(() => (typeof window === "undefined" ? "urgent" : readSort()));
  const [show, setShow] = useState<Chip>(() => (typeof window === "undefined" ? "all" : readShow()));
  // null until the job list's Ready to send read has answered (0 if it failed): announcing
  // before then would drop the ready count from the badge for a moment.
  const readyTotal = jobs.readyTotal;
  // Tasks waiting for the user's own browser (null: unknown or none).
  const [queue, setQueue] = useState<OwnBrowserQueue | null>(null);

  const apply = useCallback(
    (o: Ask[], a: Awaited<ReturnType<typeof listAsks>>, w: WaitingRequest[]) => {
      setOpen(o);
      setAnswered(a.asks);
      setWaiting(w);
      setMoreAnswered(a.asks.length >= PAGE);
      setError(null);
    },
    [],
  );

  // Reload after an answer or withdraw. On failure the old lists stay on
  // screen and the error shows above them with a retry.
  // Live (PR 6): the asks and requests as shown; one the user is in survives a refresh.
  const shown = useLiveAsks(open);
  const shownWaiting = useLiveRequests(waiting);
  const general = useMemo(() => (open ? [...groupAsks(shown).general, ...jobs.failed] : []), [open, shown, jobs.failed]);

  const load = useCallback(async () => {
    void getOwnBrowserQueue().then(setQueue);
    try {
      apply(...(await fetchAll()));
    } catch {
      setError("Could not load your questions.");
    }
  }, [apply]);

  useEffect(() => {
    let cancelled = false;
    void getOwnBrowserQueue().then((q) => !cancelled && setQueue(q));
    fetchAll()
      .then((all) => {
        if (!cancelled) apply(...all);
      })
      .catch(() => {
        if (!cancelled) setError("Could not load your questions.");
      });
    return () => {
      cancelled = true;
    };
  }, [apply]);

  // The user's own Apply in "Finish it here" was seen confirmed: its asks are closed now, re-read.
  // The assistant changed something: re-read quietly (a failed read keeps what is on screen).
  useLiveRefresh(() => {
    void getOwnBrowserQueue().then(setQueue);
    fetchAll().then((all) => apply(...all), () => {});
  });

  useEffect(() => {
    const onSubmitted = () => void load();
    window.addEventListener(LIVE_SUBMITTED_EVENT, onSubmitted);
    return () => window.removeEventListener(LIVE_SUBMITTED_EVENT, onSubmitted);
  }, [load]);

  // ONE count (owner plan §2.6): the summary line, the All chip, the chips' sum and the badge.
  const count = useMemo(
    () =>
      countNeedsYou({
        open: open ?? [], ready: jobs.inputs?.ready ?? [], readyTotal: readyTotal ?? 0,
        cvRows: jobs.inputs?.cvRows, people: jobs.inputs?.people, dropped: jobs.inputs?.dropped,
        connections: jobs.connections, failedAsks: jobs.failed.length, waiting: waiting.length, ...otherLoaded,
      }),
    [open, jobs, readyTotal, waiting.length, otherLoaded],
  );
  // A chip that empties (its last item answered) falls back to All.
  const active: Chip = show !== "all" && jobs.settled && count.byChip[show] === 0 ? "all" : show;

  const loaded = open !== null && readyTotal !== null;
  useEffect(() => {
    if (loaded) announce(count.n);
  }, [loaded, count.n]);

  async function loadOlder() {
    setLoadingMore(true);
    try {
      const page = await listAsks("answered", answered.length);
      setAnswered((prev) => [...prev, ...page.asks.filter((x) => !prev.some((p) => p.id === x.id))]);
      setMoreAnswered(page.asks.length >= PAGE);
    } catch {
      toast.error("Could not load older answers.");
    } finally {
      setLoadingMore(false);
    }
  }

  if (error && open === null) {
    return (
      <p role="alert" className="text-sm text-destructive">
        {error}
      </p>
    );
  }
  if (open === null) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="needs-you-loading">
        Loading…
      </p>
    );
  }

  return (
    <div className="flex min-w-0 flex-col gap-8" data-testid="needs-you-main">
      {error && (
        <div role="alert" className="flex items-center gap-3 text-sm text-destructive">
          <span>{error} What you see may be out of date.</span>
          <button type="button" data-testid="needs-you-retry" onClick={() => void load()} className={linkBtn}>
            Try again
          </button>
        </div>
      )}
      <NeedsYouControls
        count={count}
        settled={jobs.settled}
        show={active}
        onShow={(c) => {
          setShow(c);
          writeShow(c);
        }}
        sort={sort}
        onSort={(k) => {
          setSort(k);
          saveSort(k);
        }}
      />
      {/* One card per job (owner 2026-10-26): its ready form, CV, questions and people, most urgent first. */}
      <section aria-labelledby="open-asks" className="flex flex-col gap-3">
        <h2 id="open-asks" className="font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint">
          Waiting for you
        </h2>
        {active === "all" && queue && queue.count > 0 && <OwnBrowserQueueCard queue={queue} />}
        {active === "all" && jobs.settled && jobs.total === 0 && !jobs.connections && general.length === 0 && waiting.length > 0 && (
          <p className="text-sm text-muted-foreground" data-testid="needs-you-empty">No questions right now.</p>
        )}
        {active === "all" && !(queue && queue.count > 0) && jobs.cards.some((c) => c.ownBrowser) && <RunChromeTasks />}
        <JobCards open={shown} onChanged={load} onReport={setJobs} sort={sort} show={active} />
      </section>
      <OtherList
        hidden={active !== "all" && active !== "other"}
        onCount={setOtherLoaded}
        waiting={shownWaiting}
        questions={general}
        onChanged={load}
        renderQuestion={(a) => <AskCard key={a.id} ask={a} mode="open" onChanged={load} />}
      />

      {active === "all" && answered.length > 0 && (
        <section aria-labelledby="answered-asks" className="flex flex-col gap-3">
          <h2 id="answered-asks">
            <button
              type="button"
              aria-expanded={showAnswered}
              data-testid="answered-toggle"
              onClick={() => setShowAnswered((v) => !v)}
              className="flex items-center gap-1 font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint hover:text-foreground"
            >
              {showAnswered ? <ChevronDown aria-hidden className="size-3.5" /> : <ChevronRight aria-hidden className="size-3.5" />}
              Answered ({answered.length}{moreAnswered ? "+" : ""})
            </button>
          </h2>
          {showAnswered && (
            <ul className="flex flex-col gap-2">
              {answered.map((a) => (
                <AnsweredRow key={a.id} ask={a} onChanged={load} />
              ))}
            </ul>
          )}
          {showAnswered && moreAnswered && (
            <div>
              <button
                type="button"
                data-testid="needs-you-older"
                disabled={loadingMore}
                onClick={() => void loadOlder()}
                className={linkBtn}
              >
                {loadingMore ? "Loading…" : "Show older answers"}
              </button>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
