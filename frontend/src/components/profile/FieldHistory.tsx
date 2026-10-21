"use client";

import { useState } from "react";
import { getProfileEditHistory, type ProfileEditHistoryRow } from "@/lib/api";
import { apiErrorMessage } from "@/lib/api-error";
import { actorName, formatEditValue, formatEditedDate } from "@/lib/agent-edits";

interface FieldHistoryProps {
  /** One or more editable paths shown as one field (salary = min + max), or
   *  a whole section's worth of paths for a combined "See history" view. */
  paths: string[];
  /** Human name of the field (or section), for the button's accessible name. */
  label: string;
  /** Per-path prefix when several paths share one list ("Min" / "Max", or
   *  every field's own name for a combined section history). */
  pathLabels?: Record<string, string>;
  /** Visible button text. Defaults to "History" (one field); a combined,
   *  section-level history reads better as "See history". */
  buttonLabel?: string;
}

type Row = ProfileEditHistoryRow & { path: string };

/** A small "History" link that opens an inline, newest-first list of every
 * change to one field — the human's own saves and the assistant's edits, one
 * history (owner decision, 2026-09-25):
 *
 *   You · 25 Sep 2026 · £45k
 *   Claude · 24 Sep 2026 · £50k
 *
 * Read-only. Fetched when opened, never on page load. */
export function FieldHistory({ paths, label, pathLabels, buttonLabel }: FieldHistoryProps) {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<Row[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function toggle() {
    if (open) {
      setOpen(false);
      return;
    }
    setOpen(true);
    setError(null);
    try {
      const lists = await Promise.all(
        paths.map(async (path) =>
          (await getProfileEditHistory(path)).map((r) => ({ ...r, path }))
        )
      );
      const merged = lists.flat().sort((a, b) => (a.set_at < b.set_at ? 1 : a.set_at > b.set_at ? -1 : 0));
      setRows(merged);
    } catch (err: unknown) {
      setError(apiErrorMessage(err, "Couldn't load the history"));
    }
  }

  return (
    <div className="font-mono text-[11px]">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        aria-label={`History of ${label}`}
        className="whitespace-nowrap text-faint underline-offset-2 hover:text-foreground hover:underline"
      >
        {buttonLabel ?? "History"}
      </button>
      {open && (
        <div data-testid="field-history" className="mt-1 rounded-md border border-border px-2 py-1.5">
          {error ? (
            <p className="text-destructive">{error}</p>
          ) : rows === null ? (
            <p className="text-muted-foreground">Loading…</p>
          ) : rows.length === 0 ? (
            <p className="text-muted-foreground">No changes yet.</p>
          ) : (
            <ul className="space-y-0.5">
              {rows.map((r, i) => (
                <li key={`${r.path}-${r.set_at}-${i}`} data-testid="field-history-row">
                  {actorName(r.set_by)} · {formatEditedDate(r.set_at)} ·{" "}
                  {pathLabels?.[r.path] ? `${pathLabels[r.path]} ` : ""}
                  {r.value === null || r.value === undefined
                    ? "cleared"
                    : formatEditValue(r.value, r.path)}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
