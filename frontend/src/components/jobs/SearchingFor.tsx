"use client";

import Link from "next/link";

/**
 * The quiet line that makes the guess VISIBLE.
 *
 * WHY IT EXISTS. `search_titles` are the only words a job board ever sees. They
 * are derived — from the CV headline, the typed target titles, and the work
 * history — so they are a GUESS, and until now the guess never left the engine.
 * A user looking at wrong results had exactly one explanation available: "this
 * product does not understand me." That conclusion is unrecoverable — they
 * leave. Showing the derived query next to the way to change it converts it
 * into "wrong setting", which the user fixes themselves in one click.
 *
 * DEGRADES TO NOTHING. No titles, an undefined prop (the fetch failed, or the
 * user has no profile yet), or a list of blanks all render `null`. An empty
 * "Searching for:" label, or an error where a quiet hint should be, is worse
 * than silence — it makes the product look broken over a nicety.
 */
export function SearchingFor({ titles }: { titles?: string[] | null }) {
  const clean = (titles ?? [])
    .map((t) => (typeof t === "string" ? t.trim() : ""))
    .filter(Boolean);

  if (clean.length === 0) return null;

  return (
    <p
      data-testid="searching-for"
      className="text-xs text-muted-foreground"
    >
      <span className="text-muted-foreground/70">Searching for:</span>{" "}
      <span className="text-foreground">{clean.join(", ")}</span>{" "}
      {/* Deep link straight to the Target Job Titles field, not just to
          /profile — the fix is one field on a long page, and "go find it"
          is most of the friction this line is meant to remove. The anchor
          id lives on the field's wrapper in PreferencesForm.tsx. */}
      <Link
        href="/profile#target-job-titles"
        className="underline underline-offset-2 hover:no-underline"
      >
        change
      </Link>
    </p>
  );
}
