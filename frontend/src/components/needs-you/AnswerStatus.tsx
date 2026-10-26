import { CircleCheck, CircleDashed, CircleDot } from "lucide-react";
import { STATUS_WORD } from "@/lib/answer-status";
import type { AnswerStatus } from "@/lib/answer-status";

// The status word for one answer row. Colour is never the only signal: each word has its own shape.
const LOOK: Record<AnswerStatus, string> = {
  needs_you: "bg-amber-100 text-amber-950 dark:bg-amber-400/20 dark:text-amber-100",
  check: "border border-border text-foreground",
  ok: "bg-muted text-muted-foreground",
};
const ICON = { needs_you: CircleDot, check: CircleDashed, ok: CircleCheck } as const;

export function StatusWord({ status, testid }: { status: AnswerStatus; testid?: string }) {
  const Icon = ICON[status];
  return (
    <span data-testid={testid} data-status={status}
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded px-1.5 py-0.5 text-[11px] font-medium ${LOOK[status]}`}>
      <Icon aria-hidden className="size-3" />
      {STATUS_WORD[status]}
    </span>
  );
}

/** The one muted line under an answer: where its value came from. */
export function SourceLine({ text, testid }: { text: string; testid?: string }) {
  return (
    <span data-testid={testid} className="text-xs text-muted-foreground">
      {text}
    </span>
  );
}
