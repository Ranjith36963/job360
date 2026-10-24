"use client";

import { Fragment, useState } from "react";
import { safeHref } from "@/lib/safe-link";

/** An answer value as stored: a link (new tab) when it is an http(s) web address, else plain text.
 *  The full address always shows; it may wrap after a "/", never mid-word. "opened" marks a link the
 *  user clicked on this page (nothing is saved), so they can check it, then press Verify. */
export function AnswerText({ value }: { value: string | null | undefined }) {
  const [opened, setOpened] = useState(false);
  const href = safeHref(value);
  if (!href || !value) return <>{value}</>;
  const parts = value.split("/");
  return (
    <span className="inline-flex flex-wrap items-baseline gap-x-1.5">
      <a href={href} target="_blank" rel="noopener noreferrer" data-testid="answer-link"
        className="underline underline-offset-2 hover:text-foreground" onClick={() => setOpened(true)}>
        {parts.map((p, i) => (
          <Fragment key={i}>
            {p}
            {i < parts.length - 1 && (
              <>
                /<wbr />
              </>
            )}
          </Fragment>
        ))}
      </a>
      {opened && (
        <span className="text-xs text-muted-foreground" data-testid="answer-link-opened">
          opened
        </span>
      )}
    </span>
  );
}
