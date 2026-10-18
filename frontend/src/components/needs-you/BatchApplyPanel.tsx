"use client";

import { BatchQueueCard } from "./BatchQueueCard";
import { BatchRulesCard } from "./BatchRulesCard";
import { StartedNotConfirmed } from "./StartedNotConfirmed";

/** The right-hand panel of Needs you, beside the main column on wide screens (1280px and
 *  up) and stacked under it below that. What needs the user comes first (started submits,
 *  then the queue); the rules sit last. It scrolls with the page: a sticky panel taller than
 *  the screen hid the queue in the first real-browser check (2026-10-18). */
export function BatchApplyPanel() {
  return (
    <aside
      aria-labelledby="batch-apply"
      data-testid="batch-apply-panel"
      className="flex min-w-0 flex-col gap-4 xl:self-start"
    >
      <h2 id="batch-apply" className="font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint">
        Batch apply
      </h2>
      <StartedNotConfirmed />
      <BatchQueueCard />
      <BatchRulesCard />
    </aside>
  );
}
