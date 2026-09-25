"use client";

import { useState } from "react";
import type { AgentEdit } from "@/lib/agent-edits";
import {
  actorName,
  formatEditValue,
  formatEditedDate,
  isAssistantActor,
} from "@/lib/agent-edits";

/** "Changed by Claude · was £45k  [Keep] [Take back]" — the mark next to any
 * field an ASSISTANT has set via `PATCH /profile` (spec R11; "was X", Keep and
 * Take back, owner decisions 2026-09-25). Renders nothing when there is no
 * live assistant edit for the field (the common case), so it is safe to drop
 * next to every editable field unconditionally. The human's own web saves
 * never render one.
 *
 * "Keep" accepts the assistant's value as the human's own (it moves into the
 * base and the mark goes). "Take back" appends a clearing row — the field
 * falls back to what the CV / the form says, which is the "was" value shown.
 * Each button shows only when the caller passes its handler. */
export function EditedMark({
  edit,
  onTakeBack,
  onKeep,
}: {
  edit: AgentEdit | undefined;
  onTakeBack?: (path: string) => Promise<void>;
  onKeep?: (path: string) => Promise<void>;
}) {
  const [pending, setPending] = useState<"keep" | "take-back" | null>(null);
  if (!edit || !isAssistantActor(edit.set_by)) return null;
  const who = actorName(edit.set_by);
  const was = formatEditValue(edit.previous_value, edit.path);

  const run = (kind: "keep" | "take-back", fn: (path: string) => Promise<void>) =>
    async (e: React.MouseEvent) => {
      // The mark often sits inside a <label>; never let the click toggle or
      // focus the field it labels.
      e.preventDefault();
      e.stopPropagation();
      setPending(kind);
      try {
        await fn(edit.path);
      } finally {
        setPending(null);
      }
    };

  const buttonClass =
    "rounded-full border border-primary/30 px-2 py-0.5 text-[10px] font-medium text-primary transition-colors hover:bg-primary/10 disabled:opacity-50";

  return (
    <span className="ml-2 inline-flex flex-wrap items-center gap-1.5">
      <span
        data-testid="agent-edit-mark"
        title={`Changed by ${edit.set_by} on ${formatEditedDate(edit.set_at)}`}
        className="inline-flex items-center rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary"
      >
        Changed by {who} · was {was}
      </span>
      {onKeep && (
        <button
          type="button"
          data-testid="keep"
          aria-label={`Keep ${who}'s change`}
          disabled={pending !== null}
          onClick={run("keep", onKeep)}
          className={buttonClass}
        >
          {pending === "keep" ? "Keeping…" : "Keep"}
        </button>
      )}
      {onTakeBack && (
        <button
          type="button"
          data-testid="take-back"
          aria-label={`Take back ${who}'s change`}
          disabled={pending !== null}
          onClick={run("take-back", onTakeBack)}
          className={buttonClass}
        >
          {pending === "take-back" ? "Taking back…" : "Take back"}
        </button>
      )}
    </span>
  );
}
