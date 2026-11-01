import type { components } from "@/lib/api-types";

type Blocker = components["schemas"]["BlockerOut"];

/** Things only the user can still do on this application, other than sending (those two are the
 *  confirm step's own business). Mark Applied never refuses (owner, 2026-10-31); it warns with these. */
export function openYouItems(blockers: Blocker[] | undefined | null): string[] {
  return (blockers ?? [])
    .filter((b) => b.actor === "you" && b.kind !== "confirm_sent" && b.kind !== "outreach_send")
    .map((b) => b.label);
}

export function openItemsText(items: string[]): string {
  return `Still open: ${items.join(" · ")}. Saving marks this applied with these open.`;
}
