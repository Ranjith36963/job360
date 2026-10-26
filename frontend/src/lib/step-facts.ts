// The user's own facts for one Memory step (owner, 2026-10-26: "Add my own
// fact"). They live in `preferences.assistant_notes` — the standing lines the
// assistant reads first on every get_profile — each written as
// "<Step>: <fact>", so nothing new is stored and the assistant already reads
// them before every form. A note without a step name stays a general note.

/** The cap on one note (PROFILE_NOTE_MAX_CHARS in backend core/settings.py). */
export const NOTE_MAX_CHARS = 200;

const prefix = (step: string) => `${step}: `;

/** The step names a fact is filed under (Memory's steps; MemoryTab's `fact`). */
export const STEP_FACT_NAMES = ["About you", "Job targets", "Right to work", "Logistics", "Equality"] as const;

/** True for a note filed under a Memory step: it is shown in that step, not with the
 *  general instructions (owner, 2026-10-26: nothing shown twice). */
export function isStepFact(note: string): boolean {
  return STEP_FACT_NAMES.some((step) => note.startsWith(prefix(step)));
}

/** The facts filed under a step, without the step name, in saved order. */
export function factsFor(notes: string[], step: string): string[] {
  const p = prefix(step);
  return notes.filter((n) => n.startsWith(p)).map((n) => n.slice(p.length));
}

/** The longest fact a step can take once its name is in front. */
export function factRoom(step: string): number {
  return NOTE_MAX_CHARS - prefix(step).length;
}

/** The whole note list with one more fact for a step (blank = unchanged). */
export function withFact(notes: string[], step: string, fact: string): string[] {
  const text = fact.replace(/\s+/g, " ").trim();
  return text ? [...notes, prefix(step) + text] : notes;
}

/** The whole note list without the step's nth fact; every other note stays.
 *  An index of -1 (the fact is already gone) changes nothing. */
export function withoutFact(notes: string[], step: string, index: number): string[] {
  const p = prefix(step);
  let seen = -1;
  return notes.filter((n) => !(n.startsWith(p) && ++seen === index));
}
