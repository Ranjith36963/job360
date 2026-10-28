"use client";

import { CHIPS, GROUP_NAMES, SORTS, summaryLine } from "@/lib/needs-you-items";
import type { Chip, NeedsYouCount, SortKey } from "@/lib/needs-you-items";
import { cn, relativeTime } from "@/lib/utils";

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

// The row's own group names (the status chip on each job), so the gauges and the rows read alike.
const GAUGE_NAMES = GROUP_NAMES.slice(0, 4);
const linkBtn = "rounded px-1.5 py-1 text-xs font-medium text-muted-foreground underline-offset-2 hover:text-foreground hover:underline";

/** The quiet gauge row under the summary (owner 2026-10-27, "cockpit"): jobs per group, then how long
 *  the oldest item has waited. Plain text, no cards; a group at 0 is left out. */
function Gauges({ tiers, oldest }: { tiers: number[]; oldest: string }) {
  const parts = GAUGE_NAMES.map((name, i) => ({ name, n: tiers[i] ?? 0 })).filter((g) => g.n > 0);
  const age = relativeTime(oldest).replace(/ ago$/, "");
  if (parts.length === 0 && !age) return null;
  return (
    <p className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted-foreground" data-testid="needs-you-gauges">
      {parts.map((g) => (
        <span key={g.name}>
          {g.name} <span className="font-mono font-medium text-foreground">{g.n}</span>
        </span>
      ))}
      {age && <span>Oldest waiting <span className="font-mono font-medium text-foreground">{age}</span></span>}
    </p>
  );
}

/** The summary line with its gauges, the filter chips (single select, hidden at 0 except All) and one
 *  row holding the sort menu, Expand all / Fold all and the grey line saying what the order means
 *  (owner plan §2.1, §2.4-§2.6; one row since 2026-10-27). */
export function NeedsYouControls({ count, settled, show, onShow, sort, onSort, gauges, onFoldAll }: {
  count: NeedsYouCount;
  /** False while parts of the page are still loading: a 0 then is not yet "nothing". */
  settled: boolean;
  show: Chip;
  onShow: (chip: Chip) => void;
  sort: SortKey;
  onSort: (key: SortKey) => void;
  /** Jobs per group and the oldest open item (the job list reports them). */
  gauges?: { tiers: number[]; oldest: string };
  /** Expand all (true) / Fold all (false); absent = no job rows, no buttons. */
  onFoldAll?: (open: boolean) => void;
}) {
  const line = SORTS.find((s) => s.key === sort)?.line;
  return (
    <div className="flex min-w-0 flex-col gap-3" data-testid="needs-you-controls">
      <div className="flex flex-col gap-1">
        <p className="text-sm font-medium" data-testid="needs-you-summary">
          {count.n === 0 && !settled ? "Checking what needs you…" : summaryLine(count.n)}
        </p>
        {gauges && <Gauges tiers={gauges.tiers} oldest={gauges.oldest} />}
      </div>
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
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
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
        {onFoldAll && (
          <span className="flex items-center">
            <button type="button" className={linkBtn} data-testid="expand-all" onClick={() => onFoldAll(true)}>Expand all</button>
            <span aria-hidden className="text-faint">·</span>
            <button type="button" className={linkBtn} data-testid="fold-all" onClick={() => onFoldAll(false)}>Fold all</button>
          </span>
        )}
        <p className="text-xs text-muted-foreground" data-testid="needs-you-sort-line">{line}</p>
      </div>
    </div>
  );
}
