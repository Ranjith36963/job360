"use client";

import { useState } from "react";
import { toast } from "@/lib/toast";

/** A small text button that copies text to the clipboard. `getText` may be
 * async (a CV version's text is fetched on demand); the button is disabled
 * while it works. Toasts "Copied", or the error. */
export function CopyButton({
  getText,
  testId = "copy-button",
  className = "",
}: {
  getText: () => string | Promise<string>;
  testId?: string;
  className?: string;
}) {
  const [busy, setBusy] = useState(false);

  async function copy() {
    setBusy(true);
    try {
      const text = await getText();
      await navigator.clipboard.writeText(text);
      toast.success("Copied");
    } catch (err) {
      toast.apiError(err, "Couldn't copy");
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      type="button"
      data-testid={testId}
      disabled={busy}
      onClick={() => void copy()}
      className={`shrink-0 text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline disabled:opacity-50 ${className}`}
    >
      Copy
    </button>
  );
}
