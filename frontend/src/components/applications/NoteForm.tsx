"use client";

import { useCallback, useState } from "react";
import { toast } from "sonner";
import { recordApplicationEvent } from "@/lib/api";
import { apiErrorMessage } from "@/lib/api-error";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";

// APPLICATION_EVENT_DETAIL_MAX_CHARS (backend/src/core/settings.py) — the
// same cap `RecordEventRequest.clamp_detail` enforces server-side.
const NOTE_MAX_CHARS = 2000;

/** "Add a note" under the Timeline (fix 3, agentic UX audit) — the fastest
 * way for the seeker themself to leave a plain note on the record, the same
 * `note` event type an agent can also record via MCP. */
export function NoteForm({
  applicationId,
  onRecorded,
}: {
  applicationId: number;
  onRecorded: () => Promise<void>;
}) {
  const [text, setText] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const submit = useCallback(async () => {
    const detail = text.trim();
    if (!detail) return;
    setSubmitting(true);
    try {
      await recordApplicationEvent(applicationId, { event_type: "note", detail });
      setText("");
      await onRecorded();
    } catch (err) {
      toast.error(apiErrorMessage(err, "Could not add this note."));
    } finally {
      setSubmitting(false);
    }
  }, [applicationId, text, onRecorded]);

  return (
    <div className="mt-3 flex flex-col gap-2">
      <Textarea
        data-testid="note-input"
        value={text}
        onChange={(e) => setText(e.target.value.slice(0, NOTE_MAX_CHARS))}
        placeholder="Add a note…"
        rows={2}
        maxLength={NOTE_MAX_CHARS}
      />
      <Button
        type="button"
        size="sm"
        data-testid="note-submit"
        disabled={submitting || !text.trim()}
        onClick={() => void submit()}
        className="self-start"
      >
        {submitting ? "Adding…" : "Add note"}
      </Button>
    </div>
  );
}
