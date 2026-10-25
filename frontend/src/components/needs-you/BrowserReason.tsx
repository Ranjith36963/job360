import type { BrowserLabel } from "@/lib/browser-contract";

/** The small muted line under a browser chip: why this browser. Shown for own_browser / human only.
 *  The reason comes from the API: rendered as React text only. */
export function BrowserReason({ label, testid }: { label: BrowserLabel; testid: string }) {
  if (label.kind === "job360" || !label.reason) return null;
  return (
    <p className="text-xs text-muted-foreground" data-testid={testid}>
      {label.reason}
    </p>
  );
}
