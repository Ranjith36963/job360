// Pure helpers for "Ready to send" (S5d): the words on a card, the chip for
// where each answer came from, and the guard that decides which rows "Send all
// unflagged" may still send. No fetching, no React.

import type { ReadyAnswer, ReadyCardData, ReadyFlag } from "@/lib/api";
import { whoLabel } from "@/lib/event-labels";

export type ChipTone = "memory" | "plain" | "new" | "guess";

/** Assistant names exactly as signed; the web user reads as "you" inside a sentence. */
function nameOf(by: string): string {
  const who = whoLabel(by);
  return who.who === "you" ? "you" : who.name;
}

function valid(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "3 Oct" (or "03 Oct" when `pad`). "" for a missing or bad date. */
export function dayMonth(iso: string | null | undefined, pad = false): string {
  const d = valid(iso);
  return d ? d.toLocaleDateString(undefined, { day: pad ? "2-digit" : "numeric", month: "short" }) : "";
}

/** "08:06" in the viewer's own locale. */
export function clock(iso: string | null | undefined): string {
  const d = valid(iso);
  return d ? d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) : "";
}

/** The chip under every answer: where it came from, who saved it, when. */
export function sourceChip(a: ReadyAnswer, filledBy: string): { text: string; tone: ChipTone } {
  const saved = a.saved_by ? `saved by ${nameOf(a.saved_by)}` : "";
  const when = dayMonth(a.saved_at);
  switch (a.source) {
    case "memory":
      return { text: ["Memory", [saved, when].filter(Boolean).join(", ")].filter(Boolean).join(" · "), tone: "memory" };
    case "profile":
      return { text: "Profile", tone: "plain" };
    case "approved":
      return { text: when ? `Approved · ${when}` : "Approved", tone: "plain" };
    case "written":
      return { text: `Written new · by ${nameOf(filledBy)}`, tone: "new" };
    default:
      return { text: `Guessed · by ${nameOf(filledBy)}`, tone: "guess" };
  }
}

/** "APP-064 · brought by Claude · 08 Oct" - stored facts only. */
export function readyEyebrow(c: ReadyCardData): string {
  const id = `APP-${String(c.application_id).padStart(3, "0")}`;
  return [id, c.brought_by ? `brought by ${nameOf(c.brought_by)}` : "", dayMonth(c.brought_at, true)]
    .filter(Boolean)
    .join(" · ");
}

/** "Poolside · Paris · score 82, set by Claude" - a missing part is absent. */
export function readySub(c: ReadyCardData): string {
  const score = c.fit_score != null ? `score ${c.fit_score}${c.fit_by ? `, set by ${nameOf(c.fit_by)}` : ""}` : "";
  return [c.job_company, c.job_location, score].filter(Boolean).join(" · ");
}

/** "CV v3 · 08 Oct 08:02". */
export function docText(label: string, doc: { version: number; saved_at: string }): { label: string; detail: string } {
  return { label, detail: `v${doc.version} · ${[dayMonth(doc.saved_at, true), clock(doc.saved_at)].filter(Boolean).join(" ")}` };
}

/** A flag's words; a country code in a "not saved yet" flag becomes the country's name. */
export function flagText(flag: ReadyFlag): string {
  if (!flag.country) return flag.text;
  let name = flag.country;
  try {
    name = new Intl.DisplayNames(undefined, { type: "region" }).of(flag.country) ?? flag.country;
  } catch {
    // keep the code
  }
  return flag.text.replace(` ${flag.country} `, ` ${name} `);
}

export function splitReady(items: ReadyCardData[]): { clean: ReadyCardData[]; held: ReadyCardData[] } {
  return { clean: items.filter((c) => c.flags.length === 0), held: items.filter((c) => c.flags.length > 0) };
}

/** Where "Answer" goes for a held row: missing facts live in Memory, anything else on the card. */
export function answerHref(c: ReadyCardData): string {
  return c.flags[0]?.code === "missing" && c.flags[0].key !== "job_country"
    ? "/profile?tab=memory"
    : `/applications/${c.application_id}`;
}

/** Send-all guard: only rows that are still on the list, still unflagged, with the
 * same fill and the same CV as the user saw. The rest are skipped by name. */
export function guardSend(
  shown: ReadyCardData[],
  fresh: ReadyCardData[],
): { go: ReadyCardData[]; skipped: ReadyCardData[] } {
  const now = new Map(fresh.map((c) => [c.application_id, c]));
  const go: ReadyCardData[] = [];
  const skipped: ReadyCardData[] = [];
  for (const s of shown) {
    const f = now.get(s.application_id);
    const same =
      f !== undefined &&
      f.flags.length === 0 &&
      f.form_filled_event_id === s.form_filled_event_id &&
      (f.cv?.artifact_id ?? null) === (s.cv?.artifact_id ?? null);
    (same ? go : skipped).push(s);
  }
  return { go, skipped };
}

/** "CV v3 · letter v1 · 7 answers" / "CV v4 · no letter · 4 answers". */
export function rowDetail(c: ReadyCardData): string {
  const n = c.answers.length;
  return [
    c.cv ? `CV v${c.cv.version}` : "no CV",
    c.cover_letter ? `letter v${c.cover_letter.version}` : "no letter",
    `${n} ${n === 1 ? "answer" : "answers"}`,
  ].join(" · ");
}
