// ---------------------------------------------------------------------------
// Visa / sponsorship signal badge (slice 7, #514)
// docs/plans/2026-09-11-visa-signal/spec.md — "The badge (web, pure)"
// ---------------------------------------------------------------------------
//
// PURE display component: it renders exactly what the backend already
// computed (`visa_signal` + `needs_sponsorship`), never re-derives it.
// Job360 never knows anything about visas (spec intent) — no country rules,
// no keyword scanning live here.

/** The closed set from the backend (`APPLICATION_VISA_SIGNALS`). Typed as a
 *  plain string in props (not yet in api-types.ts — hand-typed per the spec)
 *  but this is the only vocabulary the badge understands; anything else
 *  falls through the same as "unknown". */
export type VisaSignal = "sponsors" | "no_sponsorship" | "unknown" | string;

export function VisaBadge({
  signal,
  needsSponsorship,
  detail,
}: {
  signal: VisaSignal;
  needsSponsorship?: boolean | null;
  detail?: string;
}) {
  if (signal !== "sponsors" && signal !== "no_sponsorship") {
    // "unknown" (rule #29 — the ad said nothing, show nothing) and any other
    // value fall through here too, on the same "render nothing" side.
    return null;
  }

  let pillClass: string;
  let label: string;
  let dataVisa: string;

  if (signal === "sponsors") {
    pillClass = "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300";
    label = "Sponsors visas";
    dataVisa = "sponsors";
  } else if (needsSponsorship === false) {
    pillClass = "bg-muted text-muted-foreground";
    label = "No sponsorship · not needed for you";
    dataVisa = "no_sponsorship_covered";
  } else {
    // needsSponsorship is true or null (unknown comparison) — still a red flag.
    pillClass = "bg-red-500/15 text-red-700 dark:text-red-300";
    label = "No sponsorship";
    dataVisa = "no_sponsorship";
  }

  return (
    <div className="flex flex-col gap-1">
      <span
        data-testid="visa-badge"
        data-visa={dataVisa}
        className={`rounded-full px-3 py-1 text-sm font-medium ${pillClass}`}
      >
        {label}
      </span>
      {detail && detail.trim() && (
        <p
          data-testid="visa-detail"
          className="whitespace-pre-wrap text-xs text-muted-foreground/70"
        >
          {detail}
        </p>
      )}
    </div>
  );
}
