"use client";

import { useState } from "react";
import { toast } from "sonner";
import { recordApplicationEvent } from "@/lib/api";
import { apiErrorMessage } from "@/lib/api-error";

/** "Add to batch" / "Remove from batch" - records `queued` / `unqueued` through the
 *  same web event call as every other note. The page only shows it for considering. */
export function BatchButton({
  applicationId,
  queued,
  onRecorded,
}: {
  applicationId: number;
  queued: boolean;
  onRecorded: () => Promise<void> | void;
}) {
  const [busy, setBusy] = useState(false);

  async function click() {
    setBusy(true);
    try {
      await recordApplicationEvent(
        applicationId,
        queued ? { event_type: "unqueued", payload: { reason: "user_removed" } } : { event_type: "queued" },
      );
      await onRecorded();
    } catch (err) {
      toast.error(apiErrorMessage(err, queued ? "Could not remove this from the batch." : "Could not add this to the batch."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      type="button"
      data-testid={queued ? "batch-remove" : "batch-add"}
      disabled={busy}
      onClick={() => void click()}
      className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-foreground transition-colors hover:bg-muted disabled:opacity-50"
    >
      {queued ? "Remove from batch" : "Add to batch"}
    </button>
  );
}
