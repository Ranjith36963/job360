"use client";

import { useEffect, useRef, useState } from "react";
import { Pencil, Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { getProfileEditHistory } from "@/lib/api";
import type { ProfileEditHistoryRow } from "@/lib/api";
import { actorName, formatEditedDate } from "@/lib/agent-edits";

// ── Assistant notes ────────────────────────────────────────
// "Things your assistant should know" — standing instructions, one short line
// each. The agent reads them first on every get_profile. Saved by the caller
// (Profile: the debounced preferences save; Home: a save per change), so every
// edit here is just `onChange(newList)` — no button of its own.
//
// Each line can be edited in place (Enter or click away saves, Esc cancels),
// and a History button inside the box lists the lines you once had but no
// longer do, each with an "Add back". History can only show what the profile
// edit log still holds (it keeps a bounded number of snapshots), plus lines
// removed in this view — a line older than that is not listed.

const NOTES_PATH = "preferences.assistant_notes";

// Mirrors the backend's PROFILE_NOTE_MAX_CHARS default. The server is the
// real guard (a longer note is a 422 naming the limit); this only stops the
// input from accepting what the server would refuse.
const NOTE_MAX_CHARS = 200;

const sameLine = (a: string, b: string) => a.trim().toLowerCase() === b.trim().toLowerCase();

// While an IME is composing (Japanese, Chinese, Korean…), Enter confirms the
// character and Escape cancels it — neither is meant for the note. Safari
// reports keyCode 229 instead of isComposing for the confirming key.
const composing = (e: React.KeyboardEvent) => e.nativeEvent.isComposing || e.keyCode === 229;

type PastLine = { text: string; by: string; at: string };

/** Lines that were in an earlier saved list but are not in `current`. `rows`
 * are newest-first whole-list snapshots from the profile edit log; a line is
 * reported once, with the newest snapshot that held it. */
function pastLines(rows: ProfileEditHistoryRow[], current: string[]): PastLine[] {
  const seen: PastLine[] = [];
  for (const r of rows) {
    if (!Array.isArray(r.value)) continue;
    for (const v of r.value) {
      if (typeof v !== "string" || !v.trim()) continue;
      if (current.some((c) => sameLine(c, v)) || seen.some((s) => sameLine(s.text, v))) continue;
      seen.push({ text: v, by: r.set_by, at: r.set_at });
    }
  }
  return seen;
}

function NoteRow({
  note,
  others,
  onSave,
  onRemove,
  disabled,
}: {
  note: string;
  others: string[];
  onSave: (next: string) => void;
  onRemove: () => void;
  disabled: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(note);
  // Enter unmounts the input, which blurs it: without this the same edit
  // would be saved twice.
  const finished = useRef(false);
  // Focus the field when editing starts (instead of autoFocus, which a11y lint rejects).
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  const open = () => {
    setDraft(note);
    finished.current = false;
    setEditing(true);
  };
  const finish = (commit: boolean) => {
    if (finished.current) return;
    finished.current = true;
    setEditing(false);
    const text = draft.trim();
    // Empty, unchanged or a copy of another line: keep what was there. An
    // edit never deletes a line — the cross does that.
    if (!commit || !text || text === note || others.some((o) => sameLine(o, text))) return;
    onSave(text);
  };

  return (
    <li className="flex items-start justify-between gap-2 rounded-md border border-border px-2.5 py-1.5 text-sm">
      {editing ? (
        <Input
          ref={inputRef}
          value={draft}
          maxLength={NOTE_MAX_CHARS}
          aria-label={`Edit note: ${note}`}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => finish(true)}
          onKeyDown={(e) => {
            if (composing(e)) return;
            if (e.key === "Enter") {
              e.preventDefault();
              finish(true);
            } else if (e.key === "Escape") {
              e.preventDefault();
              finish(false);
            }
          }}
          className="h-auto flex-1 py-0.5"
        />
      ) : (
        <button
          type="button"
          onClick={open}
          disabled={disabled}
          aria-label={`Edit note: ${note}`}
          className="min-w-0 flex-1 cursor-text break-words text-left disabled:cursor-default"
        >
          {note}
        </button>
      )}
      {!editing && (
        <span className="flex shrink-0 items-center gap-0.5">
          <button
            type="button"
            aria-label={`Change note: ${note}`}
            onClick={open}
            disabled={disabled}
            className="rounded-sm p-0.5 opacity-60 transition-opacity hover:opacity-100 disabled:opacity-30"
          >
            <Pencil className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            aria-label={`Remove note: ${note}`}
            onClick={onRemove}
            disabled={disabled}
            className="rounded-sm p-0.5 opacity-60 transition-opacity hover:opacity-100 disabled:opacity-30"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </span>
      )}
    </li>
  );
}

function NotesHistory({
  notes,
  gone,
  onAddBack,
  disabled,
}: {
  notes: string[];
  gone: string[];
  onAddBack: (text: string) => void;
  disabled: boolean;
}) {
  const [rows, setRows] = useState<ProfileEditHistoryRow[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    getProfileEditHistory(NOTES_PATH)
      .then((r) => {
        if (!live) return;
        setRows(r);
        setFailed(false);
      })
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [notes.length]);

  // Lines removed or changed in this very view may not be in the saved log yet.
  const fromLog = pastLines(rows ?? [], notes);
  const local: PastLine[] = gone
    .filter((g) => !notes.some((n) => sameLine(n, g)) && !fromLog.some((p) => sameLine(p.text, g)))
    .map((text) => ({ text, by: "web", at: "" }));
  const past = [...local, ...fromLog];

  return (
    <div data-testid="notes-history" className="rounded-md border border-border px-2.5 py-2 text-sm">
      <p className="mb-1.5 font-mono text-[11px] uppercase tracking-[0.09em] text-faint">
        Lines you had before
      </p>
      {/* A failed load is always said, even when lines from this view are
          listed — otherwise a partial list would look complete. */}
      {failed && <p className="mb-1.5 text-xs text-destructive">Couldn&apos;t load the history</p>}
      {past.length === 0 ? (
        failed ? null : rows === null ? (
          <p className="text-xs text-muted-foreground">Loading…</p>
        ) : (
          <p className="text-xs text-muted-foreground">Nothing removed yet.</p>
        )
      ) : (
        <ul className="space-y-1.5">
          {past.map((p) => (
            <li key={p.text} data-testid="notes-history-row" className="flex items-start justify-between gap-2">
              <span className="min-w-0 break-words">
                {p.text}
                {p.at && (
                  <span className="ml-1.5 font-mono text-[10px] text-faint">
                    {actorName(p.by)} · {formatEditedDate(p.at)}
                  </span>
                )}
              </span>
              <button
                type="button"
                aria-label={`Add back: ${p.text}`}
                onClick={() => onAddBack(p.text)}
                disabled={disabled}
                className="shrink-0 font-mono text-[11px] text-faint underline-offset-2 hover:text-foreground hover:underline disabled:opacity-50"
              >
                Add back
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function AssistantNotes({
  notes,
  onChange,
  trailing,
  footer,
  showHistory = false,
  disabled = false,
}: {
  notes: string[];
  onChange: (notes: string[]) => void;
  trailing?: React.ReactNode;
  footer?: React.ReactNode;
  /** Show the History button. Off when there is no profile yet (nothing to show). */
  showHistory?: boolean;
  /** Lock every edit control, e.g. while a Keep / Take back is changing this list. */
  disabled?: boolean;
}) {
  const [draft, setDraft] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  // Lines taken out (or reworded) since this view opened — shown in History at
  // once, before the server's log has caught up.
  const [gone, setGone] = useState<string[]>([]);

  const add = () => {
    const text = draft.trim();
    if (!text) return;
    if (!notes.some((n) => sameLine(n, text))) {
      onChange([...notes, text]);
    }
    setDraft("");
  };
  const rememberGone = (line: string) =>
    setGone((g) => (g.some((x) => sameLine(x, line)) ? g : [...g, line]));
  const remove = (i: number) => {
    rememberGone(notes[i]);
    onChange(notes.filter((_, j) => j !== i));
  };
  const edit = (i: number, next: string) => {
    rememberGone(notes[i]);
    onChange(notes.map((n, j) => (j === i ? next : n)));
  };
  const addBack = (text: string) => {
    if (!notes.some((n) => sameLine(n, text))) onChange([...notes, text]);
  };

  return (
    <div className="space-y-2" data-testid="assistant-notes">
      <div className="flex items-center justify-between gap-3">
        <Label className="font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint">
          Things your assistant should know
          {trailing}
        </Label>
        {showHistory && (
          <button
            type="button"
            onClick={() => setHistoryOpen((o) => !o)}
            aria-expanded={historyOpen}
            aria-label="History of things your assistant should know"
            className="shrink-0 font-mono text-[11px] text-faint underline-offset-2 hover:text-foreground hover:underline"
          >
            {historyOpen ? "Hide history" : "History"}
          </button>
        )}
      </div>
      <p className="text-xs text-muted-foreground -mt-1">
        One line each — e.g. &quot;Never apply to recruitment agencies&quot;. Click a line to edit it. Your
        assistant reads these first, every time.
      </p>
      {showHistory && historyOpen && (
        <NotesHistory notes={notes} gone={gone} onAddBack={addBack} disabled={disabled} />
      )}
      {notes.length > 0 && (
        <ul className="space-y-1.5">
          {notes.map((note, i) => (
            <NoteRow
              key={`${i}-${note}`}
              note={note}
              others={notes.filter((_, j) => j !== i)}
              onSave={(next) => edit(i, next)}
              onRemove={() => remove(i)}
              disabled={disabled}
            />
          ))}
        </ul>
      )}
      <div className="flex gap-2">
        <Input
          value={draft}
          maxLength={NOTE_MAX_CHARS}
          disabled={disabled}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (composing(e)) return;
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
          placeholder="Add a line for your assistant"
          aria-label="New note for your assistant"
          className="flex-1"
        />
        <Button
          type="button"
          variant="outline"
          onClick={add}
          disabled={disabled || !draft.trim()}
          aria-label="Add note"
          className="shrink-0"
        >
          <Plus className="h-3.5 w-3.5" />
        </Button>
      </div>
      {footer}
    </div>
  );
}
