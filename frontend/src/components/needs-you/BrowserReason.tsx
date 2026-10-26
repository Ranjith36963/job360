import type { BrowserLabel } from "@/lib/browser-contract";
import { LINKEDIN_OWN_BROWSER_REASON, reasonWords } from "@/lib/browser-labels";

/** The small muted line under a browser chip: why this browser. Shown for own_browser / human only.
 *  The reason is a code from the API, shown in plain words, never the raw code (React text only).
 *  LinkedIn's own-browser route is the user's choice, not a failure, and its chip already says so:
 *  no reason line for it. */
export function BrowserReason({ label, testid }: { label: BrowserLabel; testid: string }) {
  if (label.kind === "job360" || !label.reason || label.reason === LINKEDIN_OWN_BROWSER_REASON) return null;
  const words = reasonWords(label.reason);
  if (!words) return null;
  return (
    <p className="text-xs text-muted-foreground" data-testid={testid}>
      {words}
    </p>
  );
}
