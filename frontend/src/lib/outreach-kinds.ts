// Two kinds of outreach (owner 2026-11-02): JOB outreach is a person linked to an application
// (a company he applied to); NETWORK is a person with no application. Pure words and counts.
// Every count is computed from rows the server returned, never invented.
import type { Person } from "@/lib/api";

export type JobGroup = { applicationId: number; title: string; people: Person[] };

/** Job outreach: grouped under the job the person is linked to. Network: people with no job. */
export function splitPeople(people: Person[]): { jobGroups: JobGroup[]; jobPeople: Person[]; network: Person[] } {
  const jobPeople = people.filter((p) => p.jobs.length > 0);
  const network = people.filter((p) => p.jobs.length === 0);
  const groups = new Map<number, JobGroup>();
  for (const p of jobPeople) {
    const j = p.jobs[0]; // one row per person: under their first job (the row itself links every job)
    const g = groups.get(j.application_id) ?? { applicationId: j.application_id, title: `${j.job_title} · ${j.job_company}`, people: [] };
    g.people.push(p);
    groups.set(j.application_id, g);
  }
  return { jobGroups: [...groups.values()], jobPeople, network };
}

/** Drafted = at least one drafted note. Waiting on you = drafted, not sent yet, not skipped. */
export function peopleCounts(people: Person[]): { drafted: number; waiting: number } {
  const drafted = people.filter((p) => p.message_count > 0);
  return { drafted: drafted.length, waiting: drafted.filter((p) => !p.last_sent && !p.skipped).length };
}

// "Sent today" per kind waits for a server read (only the per-contact agent check has
// job_sent_today / network_sent_today), so no sent-today number is shown here yet.
export function countsText(c: { drafted: number; waiting: number }): string {
  return `${c.drafted} drafted · ${c.waiting} waiting on you`;
}
