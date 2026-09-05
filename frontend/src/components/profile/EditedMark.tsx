"use client";

import type { AgentEdit } from "@/lib/agent-edits";
import { formatEditedDate } from "@/lib/agent-edits";

/** "Edited by agent:my-token on 3 Sep 2026" — the provenance mark spec R11
 * asks for next to any field an agent has overlaid via `PATCH /profile`.
 * Renders nothing when there is no active edit for the field (the common
 * case), so it is safe to drop next to every editable field unconditionally. */
export function EditedMark({ edit }: { edit: AgentEdit | undefined }) {
  if (!edit) return null;
  return (
    <span
      data-testid="agent-edit-mark"
      title={`Edited by ${edit.set_by} on ${formatEditedDate(edit.set_at)}`}
      className="ml-2 inline-flex items-center rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium whitespace-nowrap text-primary"
    >
      Edited by {edit.set_by} · {formatEditedDate(edit.set_at)}
    </span>
  );
}
