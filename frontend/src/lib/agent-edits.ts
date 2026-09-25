// ---------------------------------------------------------------------------
// Assistant-edit provenance (spec R11, docs/plans/2026-09-05-contacts-stats;
// "was X" + Take back + one history, owner decisions 2026-09-25).
//
// `GET /profile` returns `agent_edits`: the live overlay an ASSISTANT has set
// via `PATCH /profile` (`update_profile`), one row per still-active path, each
// with `previous_value` (what the field held before). The web page renders the
// edited value IN PLACE (it already comes merged from the backend's
// `load_profile`) plus a small "Changed by <name> · was <previous>" mark with a
// "Take back" button. The human's own web saves are rows in the same history
// but never render a mark — `findAgentEdit` filters them out defensively even
// though the backend already does.
// ---------------------------------------------------------------------------

import type { AgentEdit } from "./api";

export type { AgentEdit };

/** The actor a signed-in human at the browser writes as (backend `actor_for`). */
export const WEB_ACTOR = "web";

/** True for a row an assistant wrote (`agent:…` / `token:…`). */
export function isAssistantActor(setBy: string | undefined | null): boolean {
  return Boolean(setBy) && setBy !== WEB_ACTOR;
}

/** Find the current ASSISTANT edit for one editable path, if any. */
export function findAgentEdit(
  edits: AgentEdit[] | undefined,
  path: string
): AgentEdit | undefined {
  return edits?.find((e) => e.path === path && isAssistantActor(e.set_by));
}

/** `preferences.*` paths that have no field on the preferences card — the
 * daily-check answer lives on Settings → Connect (owner decision
 * 2026-09-25), so it must not inflate a count that links to the card. */
const NOT_ON_PREFERENCES_CARD = new Set(["preferences.daily_check"]);

/** How many `preferences.*` fields on the card an assistant currently has set. */
export function countAssistantPreferenceEdits(edits: AgentEdit[] | undefined): number {
  return (edits ?? []).filter(
    (e) =>
      e.path.startsWith("preferences.") &&
      !NOT_ON_PREFERENCES_CARD.has(e.path) &&
      isAssistantActor(e.set_by)
  ).length;
}

/** "agent:Claude" → "Claude", "token:cli" → "cli", "web" → "You". */
export function actorName(setBy: string): string {
  if (!isAssistantActor(setBy)) return "You";
  const idx = setBy.indexOf(":");
  const name = idx >= 0 ? setBy.slice(idx + 1).trim() : setBy.trim();
  return name || "your assistant";
}

function isEmptyValue(value: unknown): boolean {
  if (value === null || value === undefined) return true;
  if (typeof value === "string") return value.trim() === "";
  if (Array.isArray(value)) return value.length === 0;
  return false;
}

function formatMoney(n: number): string {
  if (n >= 1000 && n % 1000 === 0) return `£${n / 1000}k`;
  return `£${n.toLocaleString("en-GB")}`;
}

/** One value as a person reads it: lists comma-joined, empty as "empty",
 *  booleans as yes/no, salaries as "£45k". Never throws on an odd shape. */
export function formatEditValue(value: unknown, path = ""): string {
  if (isEmptyValue(value)) return "empty";
  if (Array.isArray(value)) {
    return value
      .map((item) => {
        if (item && typeof item === "object") {
          const rec = item as Record<string, unknown>;
          return String(rec.title ?? rec.name ?? rec.company ?? JSON.stringify(rec));
        }
        return String(item);
      })
      .join(", ");
  }
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "number") {
    return path.includes("salary") ? formatMoney(value) : String(value);
  }
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
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
