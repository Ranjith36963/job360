"use client";

import { useCallback, useState } from "react";
import { toast } from "sonner";
import { recordApplicationEvent } from "@/lib/api";
import { apiErrorMessage } from "@/lib/api-error";
import { whoLabel } from "@/lib/event-labels";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";

// Same cap as LessonForm / RecordEventRequest.clamp_detail.
const LESSON_MAX_CHARS = 2000;

/** One lesson with Edit and Take back. History is append-only (rule M3): an
 * edit records a NEW `lesson` event whose `corrects_event_id` is this one, so
 * this one becomes superseded; "Take back" records a `note` that corrects it
 * (a correcting event retires its target, and a note is not itself a lesson,
 * so nothing replaces it). */
export function LessonRow({
  applicationId,
  eventId,
  detail,
  recordedBy,
  onChanged,
}: {
  applicationId: number;
  eventId: number;
  detail: string;
  recordedBy?: string | null;
  onChanged: () => Promise<void> | void;
}) {
  const [mode, setMode] = useState<"view" | "edit" | "confirm">("view");
  const [text, setText] = useState(detail);
  const [busy, setBusy] = useState(false);

  const save = useCallback(async () => {
    const next = text.trim();
    if (!next) return;
    setBusy(true);
    try {
      await recordApplicationEvent(applicationId, {
        event_type: "lesson",
        detail: next,
        corrects_event_id: eventId,
      });
      setMode("view");
      await onChanged();
    } catch (err) {
      toast.error(apiErrorMessage(err, "Could not save this lesson."));
    } finally {
      setBusy(false);
    }
  }, [applicationId, eventId, text, onChanged]);

  const takeBack = useCallback(async () => {
    setBusy(true);
    try {
      await recordApplicationEvent(applicationId, {
        event_type: "note",
        detail: "Lesson taken back.",
        corrects_event_id: eventId,
      });
      setMode("view");
      await onChanged();
    } catch (err) {
      toast.error(apiErrorMessage(err, "Could not take this lesson back."));
    } finally {
      setBusy(false);
    }
  }, [applicationId, eventId, onChanged]);

  const author = recordedBy ? (whoLabel(recordedBy).who === "you" ? "You" : "AI") : null;

  if (mode === "edit") {
    return (
      <div className="flex flex-col gap-2">
        <Textarea
          data-testid={`lesson-input-${eventId}`}
          value={text}
          onChange={(e) => setText(e.target.value.slice(0, LESSON_MAX_CHARS))}
          rows={3}
          maxLength={LESSON_MAX_CHARS}
        />
        <div className="flex gap-2">
          <Button
            type="button"
            size="sm"
            data-testid={`lesson-save-${eventId}`}
            disabled={busy || !text.trim()}
            onClick={() => void save()}
            className="bg-foreground text-background hover:bg-foreground/90"
          >
            {busy ? "Saving…" : "Save"}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            data-testid={`lesson-cancel-${eventId}`}
            disabled={busy}
            onClick={() => {
              setMode("view");
              setText(detail);
            }}
            className="border-border bg-card"
          >
            Cancel
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div>
      <p className="whitespace-pre-wrap font-heading text-base leading-snug">{detail}</p>
      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
        {author && (
          <span data-testid={`lesson-author-${eventId}`} className="font-mono text-faint">
            {author === "AI" ? "Written by AI" : "Written by you"}
          </span>
        )}
        {mode === "confirm" ? (
          <span className="flex flex-wrap items-center gap-2 text-muted-foreground">
            Take this lesson back? Your assistant stops reading it.
            <button
              type="button"
              data-testid={`lesson-takeback-confirm-${eventId}`}
              disabled={busy}
              onClick={() => void takeBack()}
              className="text-destructive underline-offset-2 hover:underline"
            >
              {busy ? "Taking back…" : "Yes, take back"}
            </button>
            <button
              type="button"
              data-testid={`lesson-takeback-cancel-${eventId}`}
              disabled={busy}
              onClick={() => setMode("view")}
              className="underline-offset-2 hover:text-foreground hover:underline"
            >
              Keep
            </button>
          </span>
        ) : (
          <>
            <button
              type="button"
              data-testid={`lesson-edit-${eventId}`}
              onClick={() => {
                setText(detail);
                setMode("edit");
              }}
              className="text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
            >
              Edit
            </button>
            <button
              type="button"
              data-testid={`lesson-takeback-${eventId}`}
              onClick={() => setMode("confirm")}
              className="text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
            >
              Take back
            </button>
          </>
        )}
      </div>
    </div>
  );
}
