"use client";

import { useState } from "react";
import { toast } from "sonner";
import { usePause } from "@/lib/assistant-state";
import { PAUSE_REASON_MAX, pauseUntilIso, pausedUntilText } from "@/lib/assistant-tab";
import { cn } from "@/lib/utils";
import { NUMBOX, Row } from "./BatchRuleRows";

/** "Pause until …" with an optional short reason (owner 2026-10-28). The same `usePause` state as
 *  the top bar, so both show the same thing. The reason is free text: never logged, never toasted. */
export function PauseUntil() {
  const { until, pauseUntil } = usePause();
  const [when, setWhen] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const timed = pausedUntilText(until);

  async function submit() {
    const got = pauseUntilIso(when);
    if ("error" in got) {
      toast.error(got.error);
      return;
    }
    const text = reason.trim();
    if (text.length > PAUSE_REASON_MAX) {
      toast.error(`Keep the reason to ${PAUSE_REASON_MAX} characters. Not saved.`);
      return;
    }
    setBusy(true);
    // A blank reason is not written (nothing is saved for the user that they did not type).
    const ok = await pauseUntil(got.iso, text === "" ? undefined : text);
    setBusy(false);
    if (ok) {
      toast.success("Saved.");
      setWhen("");
      setReason("");
    }
  }

  return (
    <Row title="Pause until a time" sub="Stops everything until then, then your assistant carries on. The reason is only for you.">
      <div className="flex flex-wrap items-center gap-2" data-testid="pause-until-row">
        <input
          type="datetime-local"
          aria-label="Pause until"
          data-testid="pause-until"
          value={when}
          disabled={busy}
          onChange={(e) => setWhen(e.target.value)}
          className={cn(NUMBOX, "w-56")}
        />
        <input
          type="text"
          aria-label="Reason (optional)"
          data-testid="pause-reason"
          placeholder="Reason (optional)"
          maxLength={PAUSE_REASON_MAX}
          value={reason}
          disabled={busy}
          onChange={(e) => setReason(e.target.value)}
          className="min-h-11 w-56 max-w-full rounded-lg border border-border bg-card px-3 text-sm md:min-h-9"
        />
        <button
          type="button"
          data-testid="pause-until-save"
          disabled={busy || when === ""}
          onClick={() => void submit()}
          className="min-h-11 rounded-md bg-primary px-3 text-xs font-semibold text-primary-foreground disabled:opacity-50 md:min-h-9"
        >
          Pause until then
        </button>
      </div>
      {timed && (
        <span data-testid="paused-until" className="basis-full text-[13px] font-medium text-warning">
          {timed}
        </span>
      )}
    </Row>
  );
}
