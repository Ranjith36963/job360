// ---------------------------------------------------------------------------
// Agent-edit provenance (spec R11, docs/plans/2026-09-05-contacts-stats).
//
// `GET /profile` returns `agent_edits`: the current overlay an agent has set
// via `PATCH /profile` (`update_profile`), one row per still-active path. The
// web page renders the edited value IN PLACE (it already comes merged from
// the backend's `load_profile`) plus a small "Edited by <set_by> on <date>"
// mark next to it — there is no separate list to reconcile.
// ---------------------------------------------------------------------------

import type { AgentEdit } from "./api";

export type { AgentEdit };

/** Find the current overlay row for one editable path, if any. */
export function findAgentEdit(
  edits: AgentEdit[] | undefined,
  path: string
): AgentEdit | undefined {
  return edits?.find((e) => e.path === path);
}

/** "3 Sep 2026" — short, locale-formatted date for the provenance mark. */
export function formatEditedDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}
