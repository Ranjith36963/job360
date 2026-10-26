"use client";

import { useEffect, useState, type ReactNode } from "react";
import { CircleHelp, Mail, RotateCcw, Settings, type LucideIcon } from "lucide-react";
import type { Ask } from "@/lib/api";
import { STATUS_LABEL } from "@/lib/event-labels";
import { OutcomeSuggestionCard, useOutcomeSuggestions } from "./OutcomeSuggestions";
import { SettingRequestCard, describeSettingRequest, type WaitingRequest } from "./SettingRequestCard";
import { InboxItemCard, useUnmatchedMail } from "./UnmatchedMail";

// Everything not tied to one job card, in ONE list with ONE row style (owner plan 3b): setting
// requests, questions, outcome suggestions and unmatched mail. Every summary is untrusted text
// (written by an assistant or taken from an email): rendered as React text only.

type Kind = "setting" | "question" | "outcome" | "mail";
const ICON: Record<Kind, { icon: LucideIcon; label: string }> = {
  setting: { icon: Settings, label: "Setting change" },
  question: { icon: CircleHelp, label: "Question" },
  outcome: { icon: RotateCcw, label: "Outcome" },
  mail: { icon: Mail, label: "Email" },
};

/** One compact row: icon, one-line summary, one button that opens the item's own card inline. */
function OtherRow({ kind, id, summary, verb, children }: {
  kind: Kind; id: number; summary: string; verb: "Review" | "Answer" | "Match"; children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  // Once opened the card stays mounted (only hidden), so Close never throws away a half-typed answer.
  const [opened, setOpened] = useState(false);
  const { icon: Icon, label } = ICON[kind];
  return (
    <li data-testid={`other-row-${kind}-${id}`} className="flex flex-col gap-2 rounded-lg border border-border bg-card px-3 py-2">
      <div className="flex items-center gap-2.5">
        <Icon aria-hidden className="size-4 shrink-0 text-muted-foreground" />
        <span className="sr-only">{label}: </span>
        <span className="min-w-0 flex-1 truncate text-sm" title={summary}>{summary}</span>
        <button
          type="button"
          aria-expanded={open}
          data-testid={`other-open-${kind}-${id}`}
          onClick={() => { setOpened(true); setOpen((o) => !o); }}
          className="shrink-0 rounded-md border border-border px-2.5 py-1 text-xs font-medium hover:bg-muted"
        >
          {open ? "Close" : verb}
        </button>
      </div>
      {opened && <div hidden={!open} className="pb-1">{children}</div>}
    </li>
  );
}

const firstLine = (s: string) => s.split("\n").find((l) => l.trim()) ?? s;

export function OtherList({ waiting, questions, onChanged, renderQuestion, hidden = false, onCount }: {
  /** Setting changes an assistant asked for, waiting for the user's OK. */
  waiting: WaitingRequest[];
  /** Open asks not about one loaded job. */
  questions: Ask[];
  onChanged: () => Promise<void>;
  /** The question's full card (AskCard lives on the page). */
  renderQuestion: (ask: Ask) => ReactNode;
  /** Another chip is chosen: the list still loads (its counts feed the chips), it just does not show. */
  hidden?: boolean;
  /** The outcome suggestions and unmatched mail this list loaded, for the page's one count. */
  onCount?: (n: { outcomes: number; mail: number }) => void;
}) {
  const outcomes = useOutcomeSuggestions();
  const mail = useUnmatchedMail();
  const [nOutcomes, nMail] = [outcomes.items.length, mail.items.length];
  useEffect(() => onCount?.({ outcomes: nOutcomes, mail: nMail }), [onCount, nOutcomes, nMail]);
  // A real count: the section is hidden when nothing is in it.
  const count = waiting.length + questions.length + outcomes.items.length + mail.items.length;
  if (count === 0 || hidden) return null;
  return (
    <section aria-labelledby="other-heading" data-testid="other-list" className="flex flex-col gap-3">
      <h2 id="other-heading" data-testid="other-count" className="font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint">
        Other · {count}
      </h2>
      <ul className="flex flex-col gap-2">
        {waiting.map((r) => {
          const { title, change } = describeSettingRequest(r.path, r.value, r.current_score_line);
          return (
            <OtherRow key={`s${r.id}`} kind="setting" id={r.id} verb="Review" summary={`${title}: ${firstLine(change)}`}>
              <ul><SettingRequestCard request={r} onChanged={onChanged} /></ul>
            </OtherRow>
          );
        })}
        {questions.map((a) => (
          <OtherRow key={`q${a.id}`} kind="question" id={a.id} verb="Answer"
            summary={[a.job_company, firstLine(a.question)].filter(Boolean).join(": ")}>
            <ul>{renderQuestion(a)}</ul>
          </OtherRow>
        ))}
        {outcomes.items.map((s) => (
          <OtherRow key={`o${s.event_id}`} kind="outcome" id={s.event_id} verb="Review"
            summary={`${s.job_company || s.job_title}: ${STATUS_LABEL[s.event_type] ?? s.event_type}?`}>
            <OutcomeSuggestionCard s={s} busy={outcomes.busy} onAct={(x, k) => void outcomes.act(x, k)} />
          </OtherRow>
        ))}
        {mail.items.map((it) => (
          <OtherRow key={`m${it.id}`} kind="mail" id={it.id} verb="Match" summary={`“${it.subject || "(no subject)"}” · ${it.sender}`}>
            <InboxItemCard it={it} apps={mail.apps} onResolve={mail.resolve} />
          </OtherRow>
        ))}
      </ul>
    </section>
  );
}
