"use client";

import { useCallback, useState } from "react";
import { toast } from "sonner";
import { recordApplicationEvent } from "@/lib/api";
import { apiErrorMessage } from "@/lib/api-error";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";

// APPLICATION_EVENT_DETAIL_MAX_CHARS (backend/src/core/settings.py) — the
// same cap `RecordEventRequest.clamp_detail` enforces server-side.
const LESSON_MAX_CHARS = 2000;

/** "Flag for next time" under the Timeline (slice 9, #516) — a note-to-self
 * after an application, recorded as the same `lesson` event type an agent
 * reads back before tailoring the next one. */
export function LessonForm({
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
      await recordApplicationEvent(applicationId, { event_type: "lesson", detail });
      setText("");
      await onRecorded();
    } catch (err) {
      toast.error(apiErrorMessage(err, "Could not flag this lesson."));
    } finally {
      setSubmitting(false);
    }
  }, [applicationId, text, onRecorded]);

  return (
    <div className="mt-3 flex flex-col gap-2">
      <p className="text-xs text-muted-foreground">
        A lesson your agent reads before the next application.
      </p>
      <Textarea
        data-testid="lesson-input"
        value={text}
        onChange={(e) => setText(e.target.value.slice(0, LESSON_MAX_CHARS))}
        placeholder="Flag for next time… (e.g. always mention the Kubernetes cert)"
        rows={2}
        maxLength={LESSON_MAX_CHARS}
      />
      <Button
        type="button"
        size="sm"
        data-testid="lesson-submit"
        disabled={submitting || !text.trim()}
        onClick={() => void submit()}
        className="self-start"
      >
        {submitting ? "Flagging…" : "Flag for next time"}
      </Button>
    </div>
  );
}
