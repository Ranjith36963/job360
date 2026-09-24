"use client";

import { useCallback, useState } from "react";
import { toast } from "sonner";
import { recordApplicationEvent } from "@/lib/api";
import { apiErrorMessage } from "@/lib/api-error";
import { STATUS_LABEL } from "@/lib/event-labels";
import { Button } from "@/components/ui/button";

/**
 * "What happened?" — the seeker records a status change straight onto the
 * append-only event log, the same door `recordApplicationEvent` gives an
 * agent (owner decision 1, 2026-09-24). Two of `APPLICATION_STATUS_EVENT_TYPES`
 * are left off this menu on purpose: `brought` is the birth event (never
 * re-recorded), and `applied` has its own richer flow — the header's
 * "Mark Applied" button, which creates a receipt (decision 2) — so choosing
 * "Applied" here would silently skip that receipt.
 */
const STATUS_MENU_EVENT_TYPES = [
  "replied",
  "interview_requested",
  "interview_scheduled",
  "interview_done",
  "offer",
  "rejected",
  "withdrawn",
  "ghosted",
] as const;

export function StatusMenu({
  applicationId,
  onRecorded,
}: {
  applicationId: number;
  onRecorded: () => Promise<void>;
}) {
  const [pending, setPending] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const confirm = useCallback(async () => {
    if (!pending) return;
    setSaving(true);
    try {
      await recordApplicationEvent(applicationId, { event_type: pending });
      setPending(null);
      await onRecorded();
    } catch (err) {
      toast.error(apiErrorMessage(err, "Could not record what happened."));
    } finally {
      setSaving(false);
    }
  }, [applicationId, pending, onRecorded]);

  const cancel = useCallback(() => {
    if (saving) return;
    setPending(null);
  }, [saving]);

  return (
    <div className="flex flex-col items-start gap-1.5">
      <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
        What happened?
        <select
          data-testid="status-menu"
          value=""
          onChange={(e) => {
            if (e.target.value) setPending(e.target.value);
          }}
          className="h-8 rounded-md border border-input bg-transparent px-2 text-xs"
        >
          <option value="">Choose…</option>
          {STATUS_MENU_EVENT_TYPES.map((type) => (
            <option key={type} value={type}>
              {STATUS_LABEL[type] ?? type}
            </option>
          ))}
        </select>
      </label>
      {pending && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-muted/30 px-3 py-2 text-xs">
          <span>
            Record &quot;{STATUS_LABEL[pending] ?? pending}&quot;? This is added to the history.
          </span>
          <Button
            type="button"
            size="sm"
            data-testid="status-confirm"
            disabled={saving}
            onClick={() => void confirm()}
          >
            {saving ? "Recording…" : "Confirm"}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            data-testid="status-cancel"
            disabled={saving}
            onClick={cancel}
          >
            Cancel
          </Button>
        </div>
      )}
    </div>
  );
}
