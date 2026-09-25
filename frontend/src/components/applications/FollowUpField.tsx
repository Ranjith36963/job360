"use client";

import { useCallback, useState } from "react";
import { toast } from "sonner";
import { recordApplicationEvent } from "@/lib/api";
import { apiErrorMessage } from "@/lib/api-error";
import { formatDayMonth } from "@/lib/format-date";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";

/**
 * The date field (Save / Clear) beside StatusMenu (owner decision,
 * 2026-09-25) — the same door an agent's daily-check run writes through
 * (`record_event(..., follow_up_on=...)`), just for a human at the browser.
 * Save writes a `note` event carrying the date; Clear writes a `note` event
 * carrying `follow_up_on: ""`. Either way the write is history, never a
 * silent slot edit — the Timeline shows both.
 */
export function FollowUpField({
  applicationId,
  followUpOn,
  onRecorded,
}: {
  applicationId: number;
  followUpOn: string | null;
  onRecorded: () => Promise<void>;
}) {
  const [value, setValue] = useState(followUpOn ?? "");
  const [saving, setSaving] = useState(false);

  const save = useCallback(async () => {
    if (!value) return;
    setSaving(true);
    try {
      await recordApplicationEvent(applicationId, {
        event_type: "note",
        detail: `Follow up on ${formatDayMonth(value)}`,
        follow_up_on: value,
      });
      await onRecorded();
      toast.success("Follow-up date saved");
    } catch (err) {
      toast.error(apiErrorMessage(err, "Could not save the follow-up date."));
    } finally {
      setSaving(false);
    }
  }, [applicationId, value, onRecorded]);

  const clear = useCallback(async () => {
    setSaving(true);
    try {
      await recordApplicationEvent(applicationId, {
        event_type: "note",
        detail: "Follow-up cleared",
        follow_up_on: "",
      });
      setValue("");
      await onRecorded();
      toast.success("Follow-up date cleared");
    } catch (err) {
      toast.error(apiErrorMessage(err, "Could not clear the follow-up date."));
    } finally {
      setSaving(false);
    }
  }, [applicationId, onRecorded]);

  const unchanged = value === (followUpOn ?? "");

  return (
    <div className="flex flex-col items-start gap-1.5">
      <Label htmlFor="follow-up-date" className="text-xs text-muted-foreground">
        Follow up on
      </Label>
      <div className="flex items-center gap-2">
        <Input
          id="follow-up-date"
          type="date"
          data-testid="follow-up-date"
          value={value}
          disabled={saving}
          onChange={(e) => setValue(e.target.value)}
          className="h-8 w-auto"
        />
        <Button
          type="button"
          size="sm"
          data-testid="follow-up-save"
          disabled={saving || !value || unchanged}
          onClick={() => void save()}
        >
          {saving ? "Saving…" : "Save"}
        </Button>
        {followUpOn && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            data-testid="follow-up-clear"
            disabled={saving}
            onClick={() => void clear()}
          >
            Clear
          </Button>
        )}
      </div>
    </div>
  );
}
