// ---------------------------------------------------------------------------
// Unambiguous date formatting — no hardcoded locale (the product is
// region-agnostic; a fixed locale would be wrong for some other user), but
// numeric-month formats like 9/19/2026 read as day/month in the UK and
// month/day in the US. Always spelling the month out removes the ambiguity
// while still deferring to the viewer's own locale for everything else.
// ---------------------------------------------------------------------------

/** "23 Sep 2026" (in the viewer's own locale). Returns "" for an invalid date. */
export function formatDate(value: string | number | Date): string {
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

/** "23 Sep 2026, 14:05" (in the viewer's own locale). Returns "" for an invalid date. */
export function formatDateTime(value: string | number | Date): string {
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
