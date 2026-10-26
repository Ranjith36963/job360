"use client";

import { AnswerText } from "@/components/AnswerText";
import { foldPreview, needsFold } from "@/lib/answer-text";

// The answer exactly as it will be typed: wrapped, never cut. A long one shows its start, then a
// native "Show all" fold. Untrusted text: React text only.

export function AnswerValue({ id, value }: { id: string; value: string }) {
  if (!needsFold(value)) {
    return (
      <span data-testid={`answer-full-${id}`} className="whitespace-pre-wrap break-words">
        <AnswerText value={value} />
      </span>
    );
  }
  return (
    <span className="flex flex-col gap-1 break-words">
      <span className="whitespace-pre-wrap break-words">{foldPreview(value)}</span>
      <details className="text-sm">
        <summary className="cursor-pointer text-xs font-medium text-muted-foreground hover:text-foreground">Show all</summary>
        <span data-testid={`answer-full-${id}`} className="mt-1 block whitespace-pre-wrap break-words">
          <AnswerText value={value} />
        </span>
      </details>
    </span>
  );
}

/** The red line for an answer that only describes a draft, plus the stored note kept visible. */
export function DraftMissing({ id, value }: { id: string; value: string }) {
  return (
    <span className="flex flex-col gap-0.5 break-words">
      <span data-testid={`draft-missing-${id}`} className="text-sm font-medium text-danger">
        Draft text missing: your assistant will write it on its next run
      </span>
      <span className="whitespace-pre-wrap break-words text-xs text-muted-foreground">Stored note: {value}</span>
    </span>
  );
}
