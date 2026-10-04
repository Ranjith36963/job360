"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { ASKS_CHANGED_EVENT, answerAsk, listAsks, withdrawAsk } from "@/lib/api";
import type { Ask } from "@/lib/api";
import { relativeTime } from "@/lib/utils";
import { Textarea } from "@/components/ui/textarea";
import { WhoChip } from "@/components/applications/WhoChip";

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

/** One ask. `variant="home"` is the home page's elevated card (redesign
 * slice 2): who asked + when + job on one meta line, the question in the
 * serif face, and an "Answer" button that opens the SAME answer + confirm
 * flow as the Needs-you page. Withdraw stays on the Needs-you page only. */
export function AskCard({
  ask,
  mode,
  onChanged,
  variant = "page",
}: {
  ask: Ask;
  mode: "open" | "answered";
  onChanged: () => Promise<void>;
  variant?: "page" | "home";
}) {
  const home = variant === "home";
  const [editing, setEditing] = useState(mode === "open" && !home);
  const [draft, setDraft] = useState(mode === "open" ? "" : (ask.answer ?? ""));
  const [pending, setPending] = useState<Pending>(null);
  const [busy, setBusy] = useState(false);

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

  const answerFlow = (
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
          {(mode === "answered" || home) && (
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
  );

  if (home) {
    const jobText = ask.job_company || ask.job_title;
    return (
      <li
        data-testid={`ask-${ask.id}`}
        className="flex flex-col gap-3 rounded-xl border border-border bg-card p-4 shadow-card sm:p-5"
      >
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-6">
          <div className="flex min-w-0 flex-col gap-1.5">
            <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
              <WhoChip recordedBy={ask.asked_by} />
              <span aria-hidden="true">·</span>
              <span>{relativeTime(ask.asked_at)}</span>
              {jobText && <span aria-hidden="true">·</span>}
              {jobText &&
                (ask.application_id != null ? (
                  <Link
                    href={`/applications/${ask.application_id}`}
                    className="underline-offset-2 hover:text-foreground hover:underline"
                  >
                    {jobText}
                  </Link>
                ) : (
                  <span>{jobText}</span>
                ))}
            </p>
            <p className="whitespace-pre-wrap font-heading text-xl leading-snug">{ask.question}</p>
            {ask.context && (
              <p className="whitespace-pre-wrap text-sm text-muted-foreground">{ask.context}</p>
            )}
          </div>
          {!editing && (
            <button
              type="button"
              data-testid={`ask-open-${ask.id}`}
              onClick={() => setEditing(true)}
              className="shrink-0 self-start rounded-lg bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground transition-opacity hover:opacity-90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
            >
              Answer
            </button>
          )}
        </div>
        {editing && answerFlow}
      </li>
    );
  }

  return (
    <li
      data-testid={`ask-${ask.id}`}
      className="flex flex-col gap-3 rounded-xl border border-border bg-card p-4"
    >
      <div className="flex flex-col gap-1">
        <p className="whitespace-pre-wrap font-medium">{ask.question}</p>
        {ask.context && (
          <p className="whitespace-pre-wrap text-sm text-muted-foreground">{ask.context}</p>
        )}
        <p className="text-xs text-muted-foreground">
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
          {job && " · "}
          asked by {ask.asked_by} · {relativeTime(ask.asked_at)}
        </p>
      </div>

      {mode === "answered" && !editing && (
        <div className="flex flex-col gap-1 rounded-lg bg-muted/30 p-3">
          <p className="whitespace-pre-wrap text-sm">{ask.answer}</p>
          <p className="text-xs text-muted-foreground" data-testid="ask-provenance">
            {ask.answered_by_user
              ? "You answered on Job360"
              : "Your assistant recorded your answer from chat"}
          </p>
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

      {editing && answerFlow}

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

/** Tell the sidebar badge the fresh open count (it listens for this). */
export function announce(openCount: number) {
  window.dispatchEvent(new CustomEvent(ASKS_CHANGED_EVENT, { detail: openCount }));
}

export function NeedsYou() {
  const [open, setOpen] = useState<Ask[] | null>(null);
  const [answered, setAnswered] = useState<Ask[]>([]);
  const [moreAnswered, setMoreAnswered] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const apply = useCallback(
    (o: Awaited<ReturnType<typeof listAsks>>, a: Awaited<ReturnType<typeof listAsks>>) => {
      setOpen(o.asks);
      setAnswered(a.asks);
      setMoreAnswered(a.asks.length >= PAGE);
      setError(null);
      announce(o.open_count);
    },
    [],
  );

  // Reload after an answer or withdraw. On failure the old lists stay on
  // screen and the error shows above them with a retry.
  const load = useCallback(async () => {
    try {
      const [o, a] = await Promise.all([listAsks("open"), listAsks("answered")]);
      apply(o, a);
    } catch {
      setError("Could not load your questions.");
    }
  }, [apply]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([listAsks("open"), listAsks("answered")])
      .then(([o, a]) => {
        if (!cancelled) apply(o, a);
      })
      .catch(() => {
        if (!cancelled) setError("Could not load your questions.");
      });
    return () => {
      cancelled = true;
    };
  }, [apply]);

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
    <div className="flex flex-col gap-8">
      {error && (
        <div role="alert" className="flex items-center gap-3 text-sm text-destructive">
          <span>{error} What you see may be out of date.</span>
          <button type="button" data-testid="needs-you-retry" onClick={() => void load()} className={linkBtn}>
            Try again
          </button>
        </div>
      )}
      <section aria-labelledby="open-asks" className="flex flex-col gap-3">
        <h2 id="open-asks" className="font-heading text-lg font-semibold">
          Waiting for you
        </h2>
        {open.length === 0 ? (
          <p className="text-sm text-muted-foreground" data-testid="needs-you-empty">
            Nothing needs you right now.
          </p>
        ) : (
          <ul className="flex flex-col gap-3">
            {open.map((a) => (
              <AskCard key={a.id} ask={a} mode="open" onChanged={load} />
            ))}
          </ul>
        )}
      </section>

      {answered.length > 0 && (
        <section aria-labelledby="answered-asks" className="flex flex-col gap-3">
          <h2 id="answered-asks" className="font-heading text-lg font-semibold">
            Answered
          </h2>
          <ul className="flex flex-col gap-3">
            {answered.map((a) => (
              <AskCard key={a.id} ask={a} mode="answered" onChanged={load} />
            ))}
          </ul>
          {moreAnswered && (
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
