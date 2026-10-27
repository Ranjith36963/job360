// The folded job row (owner 2026-10-27, "job seeker's cockpit"): the facts one line shows about a job,
// read only from what the page already loaded (the job's record, its ready-to-send card, its open asks).
// Pure: no fetching, no React. Every string here was written by an assistant or a job site: the row
// renders it as React text only.

import type { ApplicationDetail, ReadyCardData } from "@/lib/api";
import { closedSetLabel } from "@/lib/closed-sets";
import { countryName } from "@/lib/countries";
import { whoLabel } from "@/lib/event-labels";
import { GROUP_NAMES } from "@/lib/needs-you-items";
import { jobTier, type JobRankInput } from "@/lib/needs-you-rank";

/** What a folded row says about where a job came from. Every field is optional: unset stays unsaid. */
export type RowFacts = {
  location: string;
  /** Who brought the job ("agent:X", "web"), from its newest `brought` event. */
  broughtBy: string | null;
  broughtAt: string | null;
  /** Where it was found (a JOB_FOUND_ON value) or, failing that, the site of the ad. */
  foundOn: string | null;
  site: string | null;
  score: number | null;
};

const EMPTY: RowFacts = { location: "", broughtBy: null, broughtAt: null, foundOn: null, site: null, score: null };

function place(location: string | null | undefined, country: string | null | undefined, remote?: boolean | null): string {
  const where = (location ?? "").trim() || (country ? countryName(country.toUpperCase()) : "");
  return [where, remote === true ? "Remote" : ""].filter(Boolean).join(" · ");
}

function siteOf(url: string | null | undefined): string | null {
  try {
    return url && /^https?:\/\//i.test(url) ? new URL(url).hostname.replace(/^www\./, "") : null;
  } catch {
    return null;
  }
}

/** The facts on a job's record: the same `brought` event Ready to send reads (the newest one). */
export function factsFromDetail(d: ApplicationDetail): RowFacts {
  const brought = (d.events ?? []).filter((e) => e.event_type === "brought").sort((a, b) => b.id - a.id)[0];
  const job = d.job;
  return {
    location: place(job?.job_location, job?.country, job?.remote),
    broughtBy: brought?.recorded_by ?? null,
    broughtAt: brought?.occurred_at ?? d.created_at ?? null,
    foundOn: job?.found_on || null,
    site: siteOf(job?.job_url),
    score: d.fit?.score ?? null,
  };
}

export function factsFromReady(c: ReadyCardData): RowFacts {
  return {
    ...EMPTY,
    location: place(c.job_location, c.job_country),
    broughtBy: c.brought_by ?? null,
    broughtAt: c.brought_at ?? null,
    score: c.fit_score ?? null,
  };
}

/** The first set value of each fact, in the order given. */
export function mergeFacts(...all: (RowFacts | null | undefined)[]): RowFacts {
  const out: RowFacts = { ...EMPTY };
  for (const f of all) {
    if (!f) continue;
    for (const k of Object.keys(out) as (keyof RowFacts)[])
      if ((out[k] === null || out[k] === "") && f[k] !== null && f[k] !== "") (out as Record<string, unknown>)[k] = f[k];
  }
  return out;
}

/** "25 Oct" in the viewer's clock; "" for a bad date. */
export function shortDate(iso: string | null | undefined): string {
  const d = iso ? new Date(iso) : null;
  return d && !Number.isNaN(d.getTime()) ? d.toLocaleDateString("en-GB", { day: "numeric", month: "short" }) : "";
}

/** "by you · 25 Oct · LinkedIn": who brought it, when, and where it was found (only the parts known). */
export function cameFrom(f: RowFacts): string {
  const who = f.broughtBy ? whoLabel(f.broughtBy) : null;
  const by = who ? (who.who === "you" ? "by you" : `by ${who.name || "your assistant"}`) : "";
  const where = f.foundOn ? closedSetLabel(f.foundOn) : (f.site ?? "");
  return [by, shortDate(f.broughtAt), where].filter(Boolean).join(" · ");
}

/** The oldest open item on the job (an ask, the fill, the CV): what "since" counts from. */
export function oldestAt(j: JobRankInput): string {
  const dates = [...j.asks.map((a) => a.asked_at), j.ready?.filled_at, j.cvSavedAt].filter((d): d is string => !!d);
  return dates.reduce((best, d) => (!best || Date.parse(d) < Date.parse(best) ? d : best), "");
}

/** The group the job sits in, as the status chip names it. A job whose only open item is a person
 *  reads "Person to contact": "New connections" is the owner's name for people with NO job. */
export const nextStep = (j: JobRankInput): string => {
  const tier = jobTier(j);
  return tier === 5 ? "Person to contact" : GROUP_NAMES[tier - 1];
};
