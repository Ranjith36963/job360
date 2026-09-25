"use client";

import { useState } from "react";
import { getProfileEditHistory, type ProfileEditHistoryRow } from "@/lib/api";
import { apiErrorMessage } from "@/lib/api-error";
import { actorName, formatEditValue, formatEditedDate } from "@/lib/agent-edits";

interface FieldHistoryProps {
  /** One or more editable paths shown as one field (salary = min + max). */
  paths: string[];
  /** Human name of the field, for the button's accessible name. */
  label: string;
  /** Per-path prefix when several paths share one list ("Min" / "Max"). */
  pathLabels?: Record<string, string>;
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
export function FieldHistory({ paths, label, pathLabels }: FieldHistoryProps) {
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
    <div className="text-xs">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        aria-label={`History of ${label}`}
        className="text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
      >
        History
      </button>
      {open && (
        <div data-testid="field-history" className="mt-1 rounded-md bg-muted/30 px-2 py-1.5">
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
