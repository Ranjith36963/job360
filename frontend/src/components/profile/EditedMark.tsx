"use client";

import { useState } from "react";
import type { AgentEdit } from "@/lib/agent-edits";
import {
  actorName,
  formatEditValue,
  formatEditedDate,
  isAssistantActor,
} from "@/lib/agent-edits";

/** "Changed by Claude · was £45k  [Take back]" — the mark next to any field an
 * ASSISTANT has set via `PATCH /profile` (spec R11; "was X" + Take back, owner
 * decision 2026-09-25). Renders nothing when there is no live assistant edit
 * for the field (the common case), so it is safe to drop next to every
 * editable field unconditionally. The human's own web saves never render one.
 *
 * "Take back" appends a clearing row on the backend — the field falls back to
 * what the CV / the form says. The button shows only when the caller passes
 * `onTakeBack`. */
export function EditedMark({
  edit,
  onTakeBack,
}: {
  edit: AgentEdit | undefined;
  onTakeBack?: (path: string) => Promise<void>;
}) {
  const [pending, setPending] = useState(false);
  if (!edit || !isAssistantActor(edit.set_by)) return null;
  const who = actorName(edit.set_by);
  const was = formatEditValue(edit.previous_value, edit.path);
  return (
    <span className="ml-2 inline-flex flex-wrap items-center gap-1.5">
      <span
        data-testid="agent-edit-mark"
        title={`Changed by ${edit.set_by} on ${formatEditedDate(edit.set_at)}`}
        className="inline-flex items-center rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary"
      >
        Changed by {who} · was {was}
      </span>
      {onTakeBack && (
        <button
          type="button"
          data-testid="take-back"
          aria-label={`Take back ${who}'s change`}
          disabled={pending}
          onClick={async (e) => {
            // The mark often sits inside a <label>; never let the click
            // toggle or focus the field it labels.
            e.preventDefault();
            e.stopPropagation();
            setPending(true);
            try {
              await onTakeBack(edit.path);
            } finally {
              setPending(false);
            }
          }}
          className="rounded-full border border-primary/30 px-2 py-0.5 text-[10px] font-medium text-primary transition-colors hover:bg-primary/10 disabled:opacity-50"
        >
          {pending ? "Taking back…" : "Take back"}
        </button>
      )}
    </span>
  );
}
