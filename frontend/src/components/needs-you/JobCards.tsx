"use client";

import { getApplication, getCvToApprove, listPeople } from "@/lib/api";
import type { ApplicationDetail, Ask } from "@/lib/api";
import { buildCard, groupAsks, sortCards } from "@/lib/job-cards";
import type { JobCardData } from "@/lib/job-cards";
import { JobCard } from "./JobCard";

// A page never fetches more job records than this in one load.
const MAX_JOBS = 30;

/** Jobs with a drafted message nobody has marked sent (best effort: a failed read adds none). */
async function jobsWithUnsentMessages(): Promise<number[]> {
  try {
    const { people } = await listPeople();
    return (people ?? []).filter((p) => p.message_count > 0 && !p.last_sent).flatMap((p) => p.jobs.map((j) => j.application_id));
  } catch {
    return [];
  }
}

async function cvsToCheck(): Promise<Map<number, { artifactId: number; version: number }>> {
  try {
    const { items } = await getCvToApprove();
    return new Map(items.map((r) => [r.application_id, { artifactId: r.cv.artifact_id, version: r.cv.version }]));
  } catch {
    return new Map();
  }
}

/** Every open job-tied ask as ONE card per job, plus jobs whose only open item is someone to reach.
 *  A job record that fails to load drops its card and its asks join `general`, never the page. */
export async function loadJobCards(open: Ask[]): Promise<{ cards: JobCardData[]; general: Ask[] }> {
  const { byJob, general } = groupAsks(open);
  const [unsent, cvs] = await Promise.all([jobsWithUnsentMessages(), cvsToCheck()]);
  const all = [...new Set([...byJob.keys(), ...unsent])];
  const ids = all.slice(0, MAX_JOBS);
  for (const id of all.slice(MAX_JOBS)) general.push(...(byJob.get(id) ?? []));
  const results = await Promise.all(ids.map((id) => getApplication(id).then((d) => d, () => null)));
  const cards: JobCardData[] = [];
  results.forEach((detail, i) => {
    const asks = byJob.get(ids[i]) ?? [];
    if (!detail) {
      general.push(...asks);
      return;
    }
    const card = buildCard(detail, asks, cvs.get(detail.id) ?? null);
    if (card.asks.length > 0 || card.people.length > 0) cards.push(card);
  });
  return { cards: sortCards(cards), general };
}

/** ONE application's card, built exactly as the Needs-you page builds it (same asks filter, same CV
 *  check, same people), so the application page and Needs you can never drift. null = nothing open. */
export async function loadJobCard(detail: ApplicationDetail): Promise<JobCardData | null> {
  const open = (detail.asks ?? []).filter((a) => a.status === "open" && !a.withdrawn_at);
  const asks = groupAsks(open).byJob.get(detail.id) ?? [];
  const cvs = await cvsToCheck();
  const card = buildCard(detail, asks, cvs.get(detail.id) ?? null);
  return card.asks.length > 0 || card.people.length > 0 ? card : null;
}

export function JobCards({ cards, onChanged }: { cards: JobCardData[]; onChanged: () => Promise<void> }) {
  if (cards.length === 0) return null;
  return (
    <ul className="flex flex-col gap-3" data-testid="job-cards">
      {cards.map((c) => (
        <JobCard key={c.applicationId} card={c} onChanged={onChanged} />
      ))}
    </ul>
  );
}
