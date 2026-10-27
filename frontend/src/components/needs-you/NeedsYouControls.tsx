"use client";

import { CHIPS, SORTS, summaryLine } from "@/lib/needs-you-items";
import type { Chip, NeedsYouCount, SortKey } from "@/lib/needs-you-items";
import { cn } from "@/lib/utils";

/** The ?show= chip in the address bar; "all" when absent or unknown. */
export function readShow(): Chip {
  try {
    const v = new URLSearchParams(window.location.search).get("show");
    return CHIPS.find((c) => c.key === v)?.key ?? "all";
  } catch {
    return "all";
  }
}

/** Puts the chip in the address bar (no reload, no new history entry); All clears it. */
export function writeShow(chip: Chip): void {
  try {
    const url = new URL(window.location.href);
    if (chip === "all") url.searchParams.delete("show");
    else url.searchParams.set("show", chip);
    window.history.replaceState(window.history.state, "", url);
  } catch {
    // The chip still applies on this page; it just is not in the link.
  }
}

/** The summary line, the filter chips (single select, hidden at 0 except All) and the sort menu with
 *  one grey line saying what the order means (owner plan §2.1, §2.4-§2.6). */
export function NeedsYouControls({ count, settled, show, onShow, sort, onSort }: {
  count: NeedsYouCount;
  /** False while parts of the page are still loading: a 0 then is not yet "nothing". */
  settled: boolean;
  show: Chip;
  onShow: (chip: Chip) => void;
  sort: SortKey;
  onSort: (key: SortKey) => void;
}) {
  const line = SORTS.find((s) => s.key === sort)?.line;
  return (
    <div className="flex min-w-0 flex-col gap-3" data-testid="needs-you-controls">
      <p className="text-sm text-muted-foreground" data-testid="needs-you-summary">
        {count.n === 0 && !settled ? "Checking what needs you…" : summaryLine(count.n)}
      </p>
      {count.n > 0 && (
        <div role="group" aria-label="Show" className="-mx-1 flex min-w-0 gap-2 overflow-x-auto px-1 pb-1" data-testid="needs-you-chips">
          {CHIPS.filter((c) => c.key === "all" || count.byChip[c.key] > 0).map((c) => (
            <button
              key={c.key}
              type="button"
              aria-pressed={show === c.key}
              data-testid={`chip-${c.key}`}
              onClick={() => onShow(c.key)}
              className={cn(
                "shrink-0 whitespace-nowrap rounded-full border px-3 py-1 text-xs font-medium transition-colors",
                show === c.key ? "border-primary bg-primary text-primary-foreground" : "border-border bg-card hover:bg-muted",
              )}
            >
              {c.label} <span className="font-mono">{count.byChip[c.key]}</span>
            </button>
          ))}
        </div>
      )}
      <div className="flex flex-col gap-1">
        <label className="flex items-center gap-1.5 text-sm">
          <span className="text-muted-foreground">Sort:</span>
          <select
            data-testid="needs-you-sort"
            value={sort}
            onChange={(e) => onSort(e.target.value as SortKey)}
            className="rounded-md border border-border bg-card px-2 py-1 text-sm font-medium"
          >
            {SORTS.map((s) => (
              <option key={s.key} value={s.key}>{s.label}</option>
            ))}
          </select>
        </label>
        <p className="text-xs text-muted-foreground" data-testid="needs-you-sort-line">{line}</p>
      </div>
    </div>
  );
}
