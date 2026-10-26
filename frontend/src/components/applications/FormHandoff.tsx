"use client";

import { useState } from "react";

import { refillForm } from "@/lib/api";
import { heldText } from "@/lib/ready-to-send";
import { toast } from "@/lib/toast";
import { FinishInBrowser } from "./FinishInBrowser";

const BTN =
  "rounded-md border border-border px-2.5 py-1 text-xs font-medium text-foreground transition-colors hover:bg-muted disabled:opacity-50";

/**
 * The hand-off for a form Job360's browser filled and stopped on (CAPTCHA, login). The filled form is
 * in Job360's browser, so "Finish it here" is the primary action; the plain employer page is an EMPTY
 * form and only a small secondary link. When the filled form is gone, say so and (only when the backend
 * says `canRefill === true`) offer "Refill the form", then open the live view on success.
 * The refusal `detail` comes from the API: React text only.
 */
export function FormHandoff({
  applicationId,
  finishHere,
  canRefill,
  heldSeconds,
  formUrl,
  finishTestId = "finish-here",
  onFinished,
}: {
  applicationId: number;
  finishHere: boolean;
  canRefill?: boolean;
  heldSeconds?: number | null;
  /** The employer's page (an empty form); "" or missing = no link. */
  formUrl?: string;
  finishTestId?: string;
  onFinished?: () => void;
}) {
  const [finishing, setFinishing] = useState(false);
  const [refilling, setRefilling] = useState(false);
  const [refused, setRefused] = useState("");

  async function refill() {
    setRefilling(true);
    setRefused("");
    try {
      const res = await refillForm(applicationId);
      if (res.status === "ok") setFinishing(true);
      else setRefused(res.detail || "Job360 could not fill the form again.");
    } catch (err) {
      toast.apiError(err, "Could not fill the form again.");
    } finally {
      setRefilling(false);
    }
  }

  return (
    <div className="flex flex-col gap-2" data-testid="form-handoff">
      {finishing && (
        <FinishInBrowser
          applicationId={applicationId}
          onDone={() => {
            setFinishing(false);
            onFinished?.();
          }}
        />
      )}
      {!finishHere && !finishing && (
        <p data-testid="ask-form-closed" className="text-xs text-muted-foreground">
          The filled form was closed (Job360 restarted or 30 minutes passed).
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {finishHere && !finishing && (
          <button type="button" className={BTN} data-testid={finishTestId} onClick={() => setFinishing(true)}>
            Finish it here{heldText(heldSeconds)}
          </button>
        )}
        {!finishHere && !finishing && canRefill === true && (
          <button type="button" className={BTN} data-testid="ask-refill" disabled={refilling} onClick={() => void refill()}>
            Refill the form
          </button>
        )}
        {formUrl && (
          <a
            href={formUrl}
            target="_blank"
            rel="noopener noreferrer"
            data-testid="ask-open-empty-form"
            className="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
          >
            Open the empty form in my own browser instead
          </a>
        )}
      </div>
      {refilling && (
        <p data-testid="ask-refill-progress" className="text-xs text-muted-foreground">
          Refilling your form… (about a minute)
        </p>
      )}
      {refused && (
        <p data-testid="ask-refill-refused" role="alert" className="text-xs text-warning">
          {refused}
        </p>
      )}
    </div>
  );
}
