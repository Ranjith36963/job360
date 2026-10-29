"use client";

import { useState } from "react";
import { toast } from "sonner";
import { recordApplicationEvent } from "@/lib/api";
import { apiErrorMessage } from "@/lib/api-error";

/** A test application (an uncorrected `simulation` mark): kept out of your lists and Needs you, but
 *  still opens here. "Not a test? Undo" records a note that corrects the mark, which makes it real. */
export function TestApplicationBanner({
  applicationId,
  markEventId,
  onChanged,
}: {
  applicationId: number;
  /** The `simulation` event that marks it; null hides the Undo. */
  markEventId: number | null;
  onChanged: () => Promise<void> | void;
}) {
  const [busy, setBusy] = useState(false);

  async function undo() {
    if (markEventId == null) return;
    setBusy(true);
    try {
      await recordApplicationEvent(applicationId, {
        event_type: "note",
        detail: "Not a test application.",
        corrects_event_id: markEventId,
      });
      await onChanged();
    } catch (err) {
      toast.error(apiErrorMessage(err, "Could not undo the test mark."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      role="status"
      data-testid="test-application-banner"
      className="flex flex-col gap-2 rounded-md border border-warning/40 bg-warning/10 p-4 text-sm sm:flex-row sm:items-center sm:justify-between"
    >
      <p>Test application. It is left out of your lists and Needs you.</p>
      {markEventId != null && (
        <button
          type="button"
          data-testid="test-application-undo"
          disabled={busy}
          onClick={() => void undo()}
          className="rounded-full border border-border px-3 py-1 text-xs font-medium text-foreground disabled:opacity-50"
        >
          {busy ? "Saving…" : "Not a test? Undo"}
        </button>
      )}
    </section>
  );
}
