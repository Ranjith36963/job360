"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { getMorningCheck, updateProfileFields } from "@/lib/api";
import type { MorningCheck as MorningCheckData } from "@/lib/api";
import { PAUSE_CHANGED_EVENT, READY_CHANGED_EVENT } from "@/lib/assistant-state";
import { formatFeedTime, hasStamp, readLastVisit, writeLastVisit } from "@/lib/home";
import {
  ALWAYS_ASKS,
  BUCKETS,
  BUCKET_LABEL,
  CHECK_LOOKBACK_DAYS,
  LAST_CHECK_KEY,
  batchEdits,
  batchOn,
  batchText,
  belowText,
  pausedByText,
  quotaText,
  scoreLineText,
  sinceText,
} from "@/lib/morning-check";
import type { Bucket } from "@/lib/morning-check";
import { cn } from "@/lib/utils";

// Company, title and every label here are written by the user's assistants -
// untrusted. They are only ever rendered as React text nodes.

const sectionLabel = "font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint";
const linkBtn = "text-xs font-medium text-muted-foreground underline-offset-2 hover:text-foreground hover:underline";
const numBox = "min-h-11 w-24 rounded-lg border border-border bg-card px-3 py-1.5 text-center font-mono text-sm md:min-h-9";
const BATCH_OPTIONS = [
  { value: "off", label: "Off — ask me first" },
  { value: "on", label: "On — apply on its own" },
];

/** What the inline editor holds before Save. `score` is the raw text box. */
type Draft = { score: string; batch: boolean };

/** One row of the editor: a title and a two-way radio. */
function Switch({
  title,
  testPrefix,
  options,
  current,
  disabled,
  onPick,
}: {
  title: string;
  testPrefix: string;
  options: { value: string; label: string }[];
  current: string;
  disabled: boolean;
  onPick: (v: string) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-3 text-sm">
      <span className="min-w-[9rem] font-medium">{title}</span>
      <div role="radiogroup" aria-label={title} className="inline-flex overflow-hidden rounded-lg border border-border">
        {options.map((o, i) => (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={current === o.value}
            data-testid={`${testPrefix}-${o.value}`}
            disabled={disabled}
            onClick={() => onPick(o.value)}
            className={cn(
              "min-h-11 px-3.5 text-sm font-medium md:min-h-9",
              i > 0 && "border-l border-border",
              current === o.value ? "bg-foreground text-background" : "text-muted-foreground hover:bg-muted",
            )}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}

/**
 * The top of "Needs you" (owner decision 2026-10-09): are the assistants
 * running, under which settings, how much of today's limit is used - then what
 * happened since the user last looked: sent, blocked, waiting, failed.
 */
export function MorningCheck() {
  const [data, setData] = useState<MorningCheckData | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState<Bucket | null>(null);
  // The inline editor (owner asks 2026-10-18 / 10-19): change the score line and the
  // one Batch auto apply switch right here, then Save. Nothing is written until Save.
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [draft, setDraft] = useState<Draft>({ score: "", batch: false });
  // What the editor opened on. Save diffs against THIS, not the live strip: a
  // re-read mid-edit (pause, a Send below) must not turn an assistant's newer
  // change into an edit the user never made.
  const [base, setBase] = useState<MorningCheckData["state"] | null>(null);
  // One window per page view: a retry or a pause toggle keeps it. `last` is null on a first visit.
  // The words show the server's `since`: it clamps a stale (>30 days) or future stamp.
  const [win] = useState(() => {
    const since = readLastVisit(new Date(), LAST_CHECK_KEY, CHECK_LOOKBACK_DAYS);
    return { since, last: hasStamp(LAST_CHECK_KEY) ? since : null };
  });
  const stamped = useRef(false);

  const load = useCallback(() => {
    getMorningCheck(win.since).then(
      (d) => {
        setData(d);
        setFailed(false);
      },
      () => setFailed(true),
    );
  }, [win]);

  useEffect(() => {
    load();
  }, [load]);

  // A pause or resume from the top bar, or a Send / Don't send below, updates the strip at once.
  useEffect(() => {
    const onChange = () => load();
    window.addEventListener(PAUSE_CHANGED_EVENT, onChange);
    window.addEventListener(READY_CHANGED_EVENT, onChange);
    return () => {
      window.removeEventListener(PAUSE_CHANGED_EVENT, onChange);
      window.removeEventListener(READY_CHANGED_EVENT, onChange);
    };
  }, [load]);

  // The stamp moves forward only after the tally has been shown.
  useEffect(() => {
    if (data && !stamped.current) {
      stamped.current = true;
      writeLastVisit(data.now, LAST_CHECK_KEY);
    }
  }, [data]);

  const heading = (
    <h2 id="morning-check" className={sectionLabel}>
      Morning check (run by your assistant)
    </h2>
  );

  if (!data) {
    return (
      <section aria-labelledby="morning-check" className="flex flex-col gap-2.5">
        {heading}
        {failed ? (
          <div role="alert" className="flex items-center gap-3 text-sm text-destructive">
            <span>Could not load the morning check.</span>
            <button type="button" data-testid="mc-retry" onClick={() => load()} className={linkBtn}>
              Try again
            </button>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">Loading…</p>
        )}
      </section>
    );
  }

  const { state, tally } = data;
  const lineSet = state.apply_min_score != null;
  const sep = <span aria-hidden="true" className="text-faint">·</span>;

  const batch = batchOn(state.above_line, state.submit_mode);

  const startEdit = () => {
    setDraft({ score: state.apply_min_score == null ? "" : String(state.apply_min_score), batch });
    setBase(state);
    setEditing(true);
  };

  /** Only the fields that differ from the server go out, each as the web user. */
  async function saveEdit() {
    const t = draft.score.trim();
    const score: number | null = t === "" ? null : Number(t);
    if (score !== null && (!Number.isInteger(score) || score < 0 || score > 100)) {
      toast.error("Score line must be a whole number from 0 to 100. Not saved.");
      return;
    }
    const edits = batchEdits(base ?? state, { scoreValue: score, batch: draft.batch });
    if (edits.length === 0) {
      setEditing(false);
      return;
    }
    setSaving(true);
    try {
      await updateProfileFields(edits);
      toast.success("Saved.");
      setEditing(false);
      load();
    } catch {
      toast.error("Could not save. Try again.");
    } finally {
      setSaving(false);
    }
  }
  return (
    <section aria-labelledby="morning-check" data-testid="morning-check" className="flex flex-col gap-2.5">
      {heading}
      <div
        data-testid="mc-strip"
        className="flex flex-wrap items-center gap-x-[18px] gap-y-2 rounded-xl border border-border bg-card px-4 py-3 shadow-card"
      >
        <span className="inline-flex items-center gap-2 font-medium">
          <i
            aria-hidden="true"
            className={cn(
              "block h-2 w-2 rounded-full",
              state.paused ? "bg-warning" : "bg-primary shadow-[0_0_0_3px_var(--brand-soft)]",
            )}
          />
          {state.paused ? "Paused" : "On"}
        </span>
        {sep}
        <span data-testid="mc-score-line" className="text-[13.5px] text-muted-foreground">
          {scoreLineText(state.apply_min_score)}
        </span>
        {!lineSet && (
          <button type="button" data-testid="mc-score-line-set" onClick={startEdit} className={linkBtn}>
            Set it
          </button>
        )}
        {sep}
        <span data-testid="mc-batch" className="text-[13.5px] text-muted-foreground">
          {batchText(batch)}
        </span>
        {lineSet && (
          <>
            {sep}
            <span data-testid="mc-below" className="text-[13.5px] text-muted-foreground">
              {belowText(state.below_line)}
            </span>
          </>
        )}
        <span className="ml-auto inline-flex items-center gap-3">
          <span data-testid="mc-quota" className="font-mono text-[12.5px] font-medium">{quotaText(state.applied_today, state.daily_cap)}</span>
          {!editing && (
            <button type="button" data-testid="mc-edit" onClick={startEdit} className={linkBtn}>
              Change
            </button>
          )}
        </span>
      </div>
      <p data-testid="mc-always-asks" className="rounded-lg bg-warning-soft px-3 py-2 text-[13px] text-warning">
        {ALWAYS_ASKS}
      </p>
      {editing && (
        <form
          data-testid="mc-editor"
          aria-label="Change assistant settings"
          // Our own check and words (same as Profile -> Assistant), not the browser's bubble.
          noValidate
          onSubmit={(e) => {
            e.preventDefault();
            void saveEdit();
          }}
          className="flex flex-col gap-4 rounded-xl border border-border bg-card px-4 py-4 shadow-card"
        >
          <label className="flex flex-wrap items-center gap-3 text-sm">
            <span className="min-w-[9rem] font-medium">Score line</span>
            <input
              type="number"
              inputMode="numeric"
              min={0}
              max={100}
              aria-label="Score line"
              data-testid="mc-score-input"
              placeholder="Not set"
              value={draft.score}
              disabled={saving}
              onChange={(e) => setDraft({ ...draft, score: e.target.value })}
              className={numBox}
            />
            <span className="text-[12.5px] text-muted-foreground">0 to 100. Blank = not set; your assistant asks you.</span>
          </label>
          <Switch
            title="Batch auto apply"
            testPrefix="mc-batch"
            options={BATCH_OPTIONS}
            current={draft.batch ? "on" : "off"}
            disabled={saving}
            onPick={(v) => setDraft({ ...draft, batch: v === "on" })}
          />
          <p className="text-[12.5px] text-muted-foreground">
            On: your assistant applies on its own to every job at or above your score line. Off: it asks you before
            each one. Jobs below your score line are dropped — never applied to.
          </p>
          <div className="flex items-center gap-3">
            <button
              type="submit"
              data-testid="mc-save"
              disabled={saving}
              className="min-h-11 rounded-lg bg-foreground px-4 text-sm font-medium text-background disabled:opacity-60 md:min-h-9"
            >
              {saving ? "Saving…" : "Save"}
            </button>
            <button type="button" data-testid="mc-cancel" disabled={saving} onClick={() => setEditing(false)} className={linkBtn}>
              Cancel
            </button>
            <Link href="/connect?tab=assistant" className={cn(linkBtn, "ml-auto")}>
              More in Profile → Assistant
            </Link>
          </div>
        </form>
      )}
      {state.paused && state.paused_by && state.paused_at && (
        <p data-testid="mc-paused-by" className="px-1 font-mono text-xs text-faint">
          {pausedByText(state.paused_by, state.paused_at)}
        </p>
      )}
      <div data-testid="mc-tally" className="flex flex-wrap items-baseline gap-x-[22px] gap-y-1.5 px-1 text-sm">
        <span className="mr-1 font-mono text-xs text-faint">{sinceText(win.last ? data.since : null)}</span>
        {BUCKETS.map((b) => {
          const count = tally[b].count;
          const body = (
            <>
              {BUCKET_LABEL[b]} <span className="font-mono font-medium">{count}</span>
            </>
          );
          return count > 0 ? (
            <button
              key={b}
              type="button"
              data-testid={`tally-${b}`}
              aria-expanded={open === b}
              onClick={() => setOpen(open === b ? null : b)}
              className="min-h-11 text-foreground underline decoration-brand decoration-[1.5px] underline-offset-[3px] md:min-h-0"
            >
              {body}
            </button>
          ) : (
            <span key={b} className="text-muted-foreground">
              {body}
            </span>
          );
        })}
      </div>
      {open && tally[open].count > 0 && (
        <ul data-testid={`tally-list-${open}`} className="flex flex-col divide-y divide-border rounded-xl border border-border bg-card">
          {tally[open].items.map((it) => {
            const href = open === "sent" && it.receipt_id != null ? `/receipts/${it.receipt_id}` : `/applications/${it.application_id}`;
            return (
              <li key={`${it.application_id}-${it.at}`}>
                <Link href={href} className="flex min-h-11 flex-wrap items-baseline gap-x-3 px-4 py-2 text-sm hover:bg-muted">
                  <span>{[it.company, it.title].filter(Boolean).join(" · ") || "An application"}</span>
                  <span className="font-mono text-xs text-faint">{formatFeedTime(it.at)}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
