"use client";

import { browserFilterOptions, browserReasonWords } from "@/lib/batch-queue";
import type { QueueBrowserFilter, QueueSummary } from "@/lib/batch-queue";

const KINDS: readonly string[] = ["job360", "own_browser", "human"];

type Chip = { kind?: string; label?: string; reason?: string } | null | undefined;

/** Which browser runs one queued job, with the reason in plain words. Text from the API renders as React text only. */
export function QueueBrowserChip({ id, browser }: { id: number | string; browser: Chip }) {
  if (!browser || typeof browser.label !== "string" || !browser.kind || !KINDS.includes(browser.kind)) return null;
  const why = browserReasonWords(browser.reason);
  return (
    <>
      <span
        data-testid={`queue-browser-${id}`}
        data-kind={browser.kind}
        className="ml-2 rounded-full bg-muted px-2 py-0.5 font-mono text-[10.5px] text-muted-foreground"
      >
        {browser.label}
      </span>
      {why && (
        <span data-testid={`queue-browser-why-${id}`} className="ml-2 text-xs text-muted-foreground">
          {why}
        </span>
      )}
    </>
  );
}

/** "All 23 · Job360's browser 17 · Your own browser 6" as pressable chips; "Only you" appears only above 0. */
export function QueueBrowserFilterRow({
  summary,
  total,
  value,
  onChange,
}: {
  summary: QueueSummary | null | undefined;
  total: number;
  value: QueueBrowserFilter;
  onChange: (next: QueueBrowserFilter) => void;
}) {
  return (
    <div role="group" aria-label="Filter the queue by browser" data-testid="queue-filter" className="flex flex-wrap gap-2">
      {browserFilterOptions(summary, total).map((o) => (
        <button
          key={o.key}
          type="button"
          data-testid={`queue-filter-${o.key}`}
          aria-pressed={value === o.key}
          onClick={() => onChange(o.key)}
          className={`rounded-full px-3 py-1 text-xs font-medium ${
            value === o.key ? "bg-primary text-primary-foreground" : "bg-primary/10 text-brand hover:bg-primary/20"
          }`}
        >
          {o.text}
        </button>
      ))}
    </div>
  );
}
