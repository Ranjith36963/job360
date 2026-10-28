"use client";

import { useCallback, useRef, useState, type MouseEvent, type ReactNode } from "react";
import { Minus, Plus } from "lucide-react";
import { cn } from "@/lib/utils";

// A folded row that unfolds into its full card (owner 2026-10-27): "+ unfolds it". Shared by Needs
// you and (next) Applications. The card stays mounted while folded (only `hidden`), so typed text and
// open panels survive folding; the open state lives in memory only (useFoldSet), never in storage.

/** Which rows are open: every row folded by default; Expand all / Fold all flip the default. */
export function useFoldSet() {
  const [state, setState] = useState<{ all: boolean; flipped: ReadonlySet<number> }>({ all: false, flipped: new Set() });
  const isOpen = useCallback((id: number) => state.all !== state.flipped.has(id), [state]);
  const toggle = useCallback((id: number) => setState((s) => {
    const flipped = new Set(s.flipped);
    if (!flipped.delete(id)) flipped.add(id);
    return { all: s.all, flipped };
  }), []);
  const setAll = useCallback((all: boolean) => setState({ all, flipped: new Set() }), []);
  return { isOpen, toggle, setAll };
}
export type FoldSet = ReturnType<typeof useFoldSet>;

const INTERACTIVE = "a, button, input, select, textarea, label, summary";

/** The list the rows sit in: one framed block, a hairline between rows (dense, nothing jumps). */
export const FOLD_LIST = "flex flex-col divide-y divide-border overflow-hidden rounded-xl border border-border bg-card shadow-card";

/** One row: the + / − toggle (a real button, 44px tap area), the summary, an optional action that works
 *  without unfolding, and the card below it. A click anywhere on the summary also toggles (it presses the
 *  toggle, so the page's live hold sees an opened panel); a click on a link or button inside does not. */
export function FoldRow({ id, testid, summaryTestid, label, open, onToggle, summary, action, actionClass, children }: {
  id: string;
  testid: string;
  summaryTestid?: string;
  /** What the toggle names for a screen reader, e.g. "Emplay, Associate AI Engineer". */
  label: string;
  open: boolean;
  onToggle: () => void;
  summary: ReactNode;
  action?: ReactNode;
  /** Keeps the action slot a fixed width (e.g. "lg:w-40"), so the summary columns line up row to row. */
  actionClass?: string;
  children: ReactNode;
}) {
  const toggle = useRef<HTMLButtonElement>(null);
  const bodyId = `${id}-body`;
  const rowClick = (e: MouseEvent<HTMLDivElement>) => {
    if (e.target instanceof Element && e.target.closest(INTERACTIVE)) return;
    toggle.current?.click();
  };
  return (
    <li data-testid={testid} className={cn(open && "bg-muted/20")}>
      {/* A mouse shortcut only: the + button is the keyboard and screen-reader path. */}
      {/* eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions */}
      <div data-testid={summaryTestid} onClick={rowClick}
        className="flex cursor-pointer flex-wrap items-start gap-x-1 pr-3 pl-1 hover:bg-muted/30 lg:flex-nowrap lg:items-center">
        <button ref={toggle} type="button" aria-expanded={open} aria-controls={bodyId} onClick={onToggle}
          aria-label={`${open ? "Fold" : "Unfold"} ${label}`} data-testid={`${testid}-toggle`}
          className="grid size-11 shrink-0 place-items-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground">
          {open ? <Minus aria-hidden className="size-4" /> : <Plus aria-hidden className="size-4" />}
        </button>
        <div className="min-w-0 flex-1 py-2.5 lg:py-1">{summary}</div>
        {/* Below lg the action takes its own line under the summary (none: the empty slot hides). */}
        {(action || actionClass) && (
          <div className={cn("flex shrink-0 items-center self-center max-lg:basis-full max-lg:pb-2.5 max-lg:pl-12 max-lg:empty:hidden lg:justify-end", actionClass)}>
            {action}
          </div>
        )}
      </div>
      <div id={bodyId} hidden={!open} className="border-t border-border bg-card">
        {children}
      </div>
    </li>
  );
}
