"use client";

import { useEffect, useState } from "react";
import { getStats } from "@/lib/api";
import type { StatsResponse } from "@/lib/api";
import { closedSetLabel } from "@/lib/closed-sets";
import { countryName } from "@/lib/countries";
import { PageContainer } from "@/components/layout/PageContainer";
import { Skeleton } from "@/components/ui/skeleton";

const LABEL = "font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint";
const TH = `${LABEL} whitespace-nowrap px-3 py-2 text-right font-medium first:pl-0 first:text-left last:pr-0`;
const TD = "whitespace-nowrap px-3 py-2 text-right font-mono tabular-nums first:pl-0 first:text-left last:pr-0";

/** A 0..1 rate as "33%"; null = nothing to divide by = "—". */
export function formatRate(rate: number | null | undefined): string {
  if (rate == null) return "—";
  return `${Math.round(rate * 100)}%`;
}

type Row = {
  key: string;
  label: string;
  brought: number;
  applied: number;
  replied: number;
  interview: number;
  offer: number;
  rejected: number;
  reply_rate: number | null;
  interview_rate: number | null;
};

type Section = { id: string; title: string; rows: Row[] };

type KeyedGroup = StatsResponse["by_country"][number];

function keyed(rows: KeyedGroup[], labelOf: (key: string | null) => string): Row[] {
  return rows.map((r) => ({ ...r, key: String(r.key), label: labelOf(r.key) }));
}

function countryLabel(key: string | null): string {
  if (!key) return "Not set";
  if (key === "remote") return "Remote";
  return countryName(key);
}

/** Pure: the six job-count sections, empty ones dropped (rule #29). */
export function buildSections(stats: StatsResponse): Section[] {
  const sections: Section[] = [
    { id: "country", title: "By country", rows: keyed(stats.by_country ?? [], countryLabel) },
    {
      id: "source",
      title: "By where you found the job",
      rows: keyed(stats.by_job_source ?? [], closedSetLabel),
    },
    { id: "channel", title: "By how you applied", rows: keyed(stats.by_channel ?? [], closedSetLabel) },
    {
      id: "cv",
      title: "By CV version",
      rows: (stats.by_cv_version ?? []).map((r) => ({
        ...r,
        key: String(r.key),
        label: r.label || r.key || "Not set",
      })),
    },
    {
      id: "role",
      title: "By role",
      rows: (stats.by_role ?? []).map((r) => ({
        ...r,
        key: String(r.key),
        label: r.role || "Not set",
      })),
    },
  ];
  return sections.filter((s) => s.rows.length > 0);
}

function Overall({ overall }: { overall: StatsResponse["overall"] }) {
  const items: [number, string][] = [
    [overall.brought, "brought"],
    [overall.applied, "applied"],
    [overall.replied, "replied"],
    [overall.interview, "interviews"],
    [overall.offer, "offers"],
    [overall.rejected, "rejected"],
  ];
  return (
    <ul data-testid="stats-overall" className="grid grid-cols-2 gap-x-6 gap-y-5 sm:grid-cols-3 lg:grid-cols-6">
      {items.map(([n, label]) => (
        <li key={label}>
          <span className="block font-heading text-4xl leading-none">{n}</span>
          <span className="mt-1 block text-[12.5px] text-muted-foreground">{label}</span>
        </li>
      ))}
    </ul>
  );
}

type ScoreLine = StatsResponse["score_line"];

/** Counts only: how the user's applications sit against the user's own line. */
export function ScoreLineCard({ data }: { data: ScoreLine }) {
  const items: [string, number | string][] = [
    ["Score line", data.line == null ? "Not set" : data.line],
    ["At or above", data.above],
    ["Below", data.below],
    ["No score", data.unscored],
  ];
  return (
    <section aria-labelledby="stats-score-line" data-testid="stats-score-line">
      <h2 id="stats-score-line" className={`${LABEL} mb-2`}>
        Your score line
      </h2>
      <ul className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
        {items.map(([label, n]) => (
          <li key={label}>
            <span className="block font-heading text-3xl leading-none">{n}</span>
            <span className="mt-1 block text-[12.5px] text-muted-foreground">{label}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function JobTable({ section }: { section: Section }) {
  return (
    <section aria-labelledby={`stats-${section.id}`} data-testid={`stats-section-${section.id}`}>
      <h2 id={`stats-${section.id}`} className={`${LABEL} mb-2`}>
        {section.title}
      </h2>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[34rem] border-collapse text-sm">
          <thead>
            <tr>
              <th scope="col" className={TH} />
              <th scope="col" className={TH}>Brought</th>
              <th scope="col" className={TH}>Applied</th>
              <th scope="col" className={TH}>Replied</th>
              <th scope="col" className={TH}>Interviews</th>
              <th scope="col" className={TH}>Reply %</th>
              <th scope="col" className={TH}>Interview %</th>
            </tr>
          </thead>
          <tbody>
            {section.rows.map((r) => (
              <tr key={r.key} data-testid="stats-row" className="border-t border-border">
                <th scope="row" className={`${TD} font-sans font-normal`}>{r.label}</th>
                <td className={TD}>{r.brought}</td>
                <td className={TD}>{r.applied}</td>
                <td className={TD}>{r.replied}</td>
                <td className={TD}>{r.interview}</td>
                <td className={TD}>{formatRate(r.reply_rate)}</td>
                <td className={TD}>{formatRate(r.interview_rate)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function PeopleTable({ rows }: { rows: StatsResponse["by_contact_found_via"] }) {
  return (
    <section aria-labelledby="stats-people" data-testid="stats-section-people">
      <h2 id="stats-people" className={`${LABEL} mb-2`}>
        By where you found the person
      </h2>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[26rem] border-collapse text-sm">
          <thead>
            <tr>
              <th scope="col" className={TH} />
              <th scope="col" className={TH}>Contacts</th>
              <th scope="col" className={TH}>Messages sent</th>
              <th scope="col" className={TH}>Replied</th>
              <th scope="col" className={TH}>Reply %</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={String(r.key)} data-testid="stats-row" className="border-t border-border">
                <th scope="row" className={`${TD} font-sans font-normal`}>{closedSetLabel(r.key)}</th>
                <td className={TD}>{r.contacts}</td>
                <td className={TD}>{r.outreach_sent}</td>
                <td className={TD}>{r.outreach_replied}</td>
                <td className={TD}>{formatRate(r.reply_rate)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

type SitesBlock = NonNullable<StatsResponse["sites"]>;

/** "Login page 2, Captcha 1" - the blocked counts by reason, facts only. */
export function blockedText(blocked: Record<string, number>): string {
  return Object.entries(blocked)
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([reason, n]) => {
      const words = closedSetLabel(reason);
      return `${words.charAt(0).toUpperCase()}${words.slice(1)} ${n}`;
    })
    .join(", ");
}

/** Counts per site (the form's host). No ranking, no judgement words. */
export function SitesTable({ data }: { data: SitesBlock }) {
  return (
    <section aria-labelledby="stats-sites" data-testid="stats-section-sites">
      <h2 id="stats-sites" className={`${LABEL} mb-2`}>
        By site
      </h2>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[34rem] border-collapse text-sm">
          <thead>
            <tr>
              <th scope="col" className={TH}>Site</th>
              <th scope="col" className={TH}>Applications (n)</th>
              <th scope="col" className={TH}>Forms filled</th>
              <th scope="col" className={TH}>Applied</th>
              <th scope="col" className={TH}>Blocked</th>
            </tr>
          </thead>
          <tbody>
            {data.sites.map((r) => (
              <tr key={r.host} data-testid="stats-site-row" className="border-t border-border">
                <th scope="row" className={`${TD} font-sans font-normal`}>{r.host}</th>
                <td className={TD}>{r.n}</td>
                <td className={TD}>{r.forms_filled}</td>
                <td className={TD}>{r.applied}</td>
                <td className={TD}>
                  {r.blocked_total}
                  {r.blocked_total > 0 && (
                    <span data-testid="stats-site-blocked" className="block font-sans text-[11px] text-muted-foreground">
                      {blockedText(r.blocked)}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data.note && <p className="mt-2 text-[12.5px] text-muted-foreground">{data.note}</p>}
      {data.truncated && <p className="mt-1 text-[12.5px] text-muted-foreground">Only part of the list is shown.</p>}
    </section>
  );
}

/** Counts and rates off the stored record. No judgement words, no colours
 * that mean good or bad - the assistant reads these and judges. */
export function StatsClient() {
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    getStats("sites")
      .then((res) => {
        if (alive) setStats(res);
      })
      .catch(() => {
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  const sections = stats ? buildSections(stats) : [];
  const people = stats?.by_contact_found_via ?? [];
  const scoreLine = stats?.score_line;

  return (
    <PageContainer className="flex flex-col gap-8 py-8">
      <div>
        <h1 className="font-heading text-3xl font-normal tracking-tight">Stats</h1>
        <p className="text-muted-foreground">
          Counts from your records. Your assistant reads these and judges; Job360 only counts.
        </p>
      </div>

      {failed ? (
        <p data-testid="stats-error" className="text-sm text-muted-foreground">
          Couldn&apos;t load this — refresh to try again.
        </p>
      ) : !stats ? (
        <div data-testid="stats-loading" className="flex flex-col gap-4" aria-busy="true">
          <Skeleton className="h-10 w-full max-w-xl" />
          <Skeleton className="h-32 w-full" />
        </div>
      ) : (
        <>
          <Overall overall={stats.overall} />
          {scoreLine && <ScoreLineCard data={scoreLine} />}
          {sections.map((s) => (
            <JobTable key={s.id} section={s} />
          ))}
          {people.length > 0 && <PeopleTable rows={people} />}
          {stats.sites && stats.sites.sites.length > 0 && <SitesTable data={stats.sites} />}
        </>
      )}
    </PageContainer>
  );
}
