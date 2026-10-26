// Helpers for showing an answer exactly as it will be typed. Pure, no React.

/** Longer than this (characters) or more than FOLD_LINES lines: the first part shows, the rest folds. */
export const FOLD_CHARS = 280;
export const FOLD_LINES = 4;

/** True when the value is long enough to fold behind "Show all". */
export function needsFold(value: string): boolean {
  return value.length > FOLD_CHARS || value.split("\n").length > FOLD_LINES;
}

/** The first part of a long value (at most FOLD_CHARS characters and FOLD_LINES lines), with "…". */
export function foldPreview(value: string): string {
  const lines = value.split("\n").slice(0, FOLD_LINES).join("\n");
  const cut = lines.length > FOLD_CHARS ? lines.slice(0, FOLD_CHARS) : lines;
  return `${cut.trimEnd()}…`;
}

const STARTS_DRAFT = /^(to be drafted|will draft|drafted|draft)(?![a-z])/;
const CONTAINS_PLACEHOLDER = [
  "drafted from",
  "from profile facts",
  "from cv",
  "from the cv",
  "based on profile",
  "see profile",
  "placeholder",
  "[...]",
];
const ONLY_DOTS = /^[\s.…]*$/;
// Questions that ask for writing, not a fact. "how" and "tell us" only in their essay forms.
const ESSAY_QUESTION =
  /\b(describe|explain|essay|cover letter|motivation|why)\b|\btell us (about|why|how|what)\b|\bhow (would|do|did|have|has) you\b(?!.*\bhear\b)/i;

/** True when the stored answer describes a draft instead of being the draft (for example
 *  "drafted from profile facts"). Narrow on purpose: short factual answers are not placeholders. */
export function looksLikePlaceholder(question: string, answer: string | null | undefined): boolean {
  const a = (answer ?? "").trim();
  if (a === "") return false;
  const low = a.toLowerCase();
  if (STARTS_DRAFT.test(low)) return true;
  if (ONLY_DOTS.test(a)) return true;
  if (/\btbd\b/.test(low)) return true;
  if (CONTAINS_PLACEHOLDER.some((p) => low.includes(p))) return true;
  return ESSAY_QUESTION.test(question ?? "") && a.length < 60;
}
