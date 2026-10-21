"use client";

import { useState } from "react";
import { markCvSeen } from "@/lib/api";
import type { ApplicationControls, DecisionMark } from "@/lib/api";
import { toast } from "@/lib/toast";
import { formatDate } from "@/lib/format-date";

/** "Checked on the website / in chat by Claude" - plain words for who + where. */
export function describeMark(mark: DecisionMark): string {
  const where = mark.where === "chat" ? "in chat" : "on the website";
  const who = mark.by && mark.by !== "web" ? ` (${mark.by.replace(/^(token|agent):/, "")})` : "";
  return `${where}${who}`;
}

/**
 * "I've checked this CV" (S3, owner decision 2026-10-08) - next to the latest CV
 * version. The user's own click records that they read it, so an assistant on
 * auto-submit may send it. Shows "Checked ✓ <date>" once done. A new CV version
 * starts unchecked again (the parent passes `seen = null`). Session-only on the
 * server; it has no assistant twin.
 */
export function CvSeenButton({
  applicationId,
  artifactId,
  seen,
  onChanged,
}: {
  applicationId: number;
  artifactId: number;
  seen: DecisionMark | null;
  onChanged?: (next: ApplicationControls) => void;
}) {
  const [busy, setBusy] = useState(false);

  if (seen) {
    return (
      <span data-testid="cv-seen-done" className="shrink-0 text-xs text-brand" title={describeMark(seen)}>
        Checked ✓ {formatDate(seen.at)}
      </span>
    );
  }

  async function click() {
    setBusy(true);
    try {
      onChanged?.(await markCvSeen(applicationId, artifactId));
    } catch (err) {
      toast.apiError(err, "Couldn't save that you checked this CV");
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      type="button"
      data-testid="cv-seen-button"
      disabled={busy}
      onClick={() => void click()}
      className="shrink-0 text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline disabled:opacity-50"
    >
      I&apos;ve checked this CV
    </button>
  );
}
