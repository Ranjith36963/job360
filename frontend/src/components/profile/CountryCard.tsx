"use client";

import { useState, type ReactNode } from "react";
import { countryName } from "@/lib/countries";

interface CountryCardProps {
  code: string;
  /** How many of the four right-to-work facts are saved. */
  saved: number;
  total: number;
  /** The first saved fact and who saved it, for the collapsed line. */
  firstFact?: string;
  defaultOpen?: boolean;
  /** The rows (right to work, then this country's logistics). */
  children: ReactNode;
}

/** One country: a line when closed, the facts when open. The amber chip counts
 *  what is not saved yet (rule #29: nothing is invented to fill it). */
export function CountryCard({ code, saved, total, firstFact, defaultOpen = false, children }: CountryCardProps) {
  const [open, setOpen] = useState(defaultOpen);
  const missing = total - saved;
  const summary = saved === total || !firstFact ? `${saved} of ${total} saved` : firstFact;
  return (
    <div data-testid={`country-card-${code}`} className="mt-2.5 rounded-xl border border-border bg-card">
      <div className="flex flex-wrap items-center justify-between gap-x-3.5 gap-y-1.5 px-4 py-3">
        <span className="font-semibold">{countryName(code)}</span>
        <span className="text-[13px] text-muted-foreground">{summary}</span>
        {missing > 0 && (
          <span
            data-testid={`country-missing-${code}`}
            className="rounded-full bg-warning-soft px-2.5 py-0.5 text-xs font-medium text-warning"
          >
            {missing} not saved yet
          </span>
        )}
        <button
          type="button" aria-expanded={open} aria-label={`${open ? "Close" : "Open"} ${countryName(code)}`}
          onClick={() => setOpen((o) => !o)}
          className="min-h-11 text-sm text-muted-foreground underline-offset-2 hover:text-foreground hover:underline md:min-h-8"
        >
          {open ? "Close" : "Open"}
        </button>
      </div>
      {open && <div className="px-4 pb-1">{children}</div>}
    </div>
  );
}
