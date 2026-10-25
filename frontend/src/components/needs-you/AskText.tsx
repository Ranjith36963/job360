import { splitAskText } from "@/lib/job-cards";

// Ask text is written by the user's assistant: untrusted, rendered as React text only.

/** An ask's question as one line, and the rest (plus its context) behind a native "Details". A short
 *  question with a short context shows both as they are. */
export function AskText({
  id,
  question,
  context = "",
  headClass,
  restClass = "whitespace-pre-wrap text-xs text-muted-foreground",
}: {
  id: number;
  question: string;
  context?: string;
  headClass: string;
  restClass?: string;
}) {
  const { head, rest } = splitAskText(question);
  const ctx = context.trim();
  const ctxLong = splitAskText(ctx).rest !== "";
  const hide = rest !== "" || ctxLong;
  return (
    <>
      <p className={`whitespace-pre-wrap ${headClass}`}>{hide ? head : question}</p>
      {!hide && ctx && <p className={restClass}>{ctx}</p>}
      {hide && (
        <details data-testid={`details-${id}`} className="text-xs text-muted-foreground">
          <summary className="cursor-pointer select-none">Details</summary>
          <div className="mt-1 flex flex-col gap-1">
            {rest && <p className="whitespace-pre-wrap">{rest}</p>}
            {ctx && <p className="whitespace-pre-wrap">{ctx}</p>}
          </div>
        </details>
      )}
    </>
  );
}
