"use client";

import Link from "next/link";

/** Your batch rules (daily limit, per-company, weekly, wait, how your assistant applies,
 *  never apply to) are assistant settings, so they live on Connect your assistant (owner
 *  rule, 2026-10-26). Needs you only points there; it has no inputs. */
export function BatchRulesCard() {
  return (
    <p data-testid="batch-rules" className="px-1 text-[12.5px] text-muted-foreground">
      Your rules:{" "}
      <Link
        href="/connect?tab=assistant"
        data-testid="batch-rules-link"
        className="underline underline-offset-2 hover:text-foreground"
      >
        Connect your assistant &rsaquo; Assistant configure
      </Link>
    </p>
  );
}
