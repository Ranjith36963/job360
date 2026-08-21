// ---------------------------------------------------------------------------
// Staleness badge vocabulary — matches the REAL values written to the DB.
//
// VERIFIED LIVE (2026-08-15, all 10,257 prod jobs): the only values
// `staleness_state` ever holds are the lowercase strings from
// `backend/src/services/ghost_detection.py:27-31` (StalenessState enum):
//   active (4,769) | possibly_stale (525) | likely_stale (4,719) | confirmed_expired (244)
//
// The frontend used to compare against uppercase literals (ACTIVE / STALE /
// GHOST / CONFIRMED_EXPIRED) that never existed in the DB, so every card —
// including the 4,769 genuinely active ones — fell through to the default
// "unknown" branch and rendered a gray badge printing the raw DB string.
// This file is the ONE place that mapping lives; JobCard and JobDetailClient
// both import it instead of hand-rolling their own switch.
// ---------------------------------------------------------------------------

export type StalenessBadge = {
  /** Short label shown on the badge itself. */
  label: string;
  /** Tooltip / longer explanation shown on hover. */
  description: string;
  /** Tailwind classes for the badge's text + border colour. */
  colorClass: string;
};

/**
 * Map a raw `job.staleness_state` DB value to a badge, or `null` when no
 * badge should render (active listings, and null/unrecognised values — an
 * unrecognised value should never scare the user with a red/amber badge for
 * a listing we have no real signal about).
 */
export function stalenessBadge(state?: string | null): StalenessBadge | null {
  switch (state) {
    case "active":
      return null;
    case "possibly_stale":
      return {
        label: "Possibly stale",
        description: "Listing may be stale — check before applying",
        colorClass: "text-amber-400 border-amber-400/30",
      };
    case "likely_stale":
      return {
        label: "May be filled",
        description: "This role has likely already been filled or closed",
        colorClass: "text-amber-400 border-amber-400/30",
      };
    case "confirmed_expired":
      return {
        label: "Expired",
        description: "This listing has been confirmed expired",
        colorClass: "text-red-400 border-red-400/30",
      };
    default:
      // null / undefined / unknown values — no badge.
      return null;
  }
}
