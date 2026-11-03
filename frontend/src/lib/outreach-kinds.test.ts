import { describe, expect, it } from "vitest";
import type { Person } from "@/lib/api";
import { LINKEDIN_LIMIT_NOTE, OUTREACH_LIMITS, PEOPLE_HUNT_HELP } from "@/lib/assistant-tab";
import { paceText } from "@/lib/browser-labels";
import { countsText, peopleCounts, splitPeople } from "./outreach-kinds";

const person = (over: Partial<Person>): Person =>
  ({ contact_ids: [1], name: "A", jobs: [], message_count: 0, last_sent: null, skipped: false, ...over }) as unknown as Person;
const job = (id: number) => ({ application_id: id, job_title: `T${id}`, job_company: `C${id}` });

describe("two kinds of outreach", () => {
  const rows = [
    person({ contact_ids: [1], jobs: [job(10)], message_count: 1 }),
    person({ contact_ids: [2], jobs: [job(10), job(11)], message_count: 2, last_sent: {} as Person["last_sent"] }),
    person({ contact_ids: [3], message_count: 1 }),
    person({ contact_ids: [4], message_count: 1, skipped: true }),
    person({ contact_ids: [5] }),
  ];
  it("splits by application: linked = job outreach grouped under each job, unlinked = network", () => {
    const s = splitPeople(rows);
    expect(s.jobPeople.map((p) => p.contact_ids[0])).toEqual([1, 2]);
    expect(s.network.map((p) => p.contact_ids[0])).toEqual([3, 4, 5]);
    expect(s.jobGroups.map((g) => [g.applicationId, g.people.length])).toEqual([[10, 2]]);
    expect(s.jobGroups[0].title).toBe("T10 · C10");
  });
  it("counts drafted and waiting from the rows", () => {
    const s = splitPeople(rows);
    expect(peopleCounts(s.jobPeople)).toEqual({ drafted: 2, waiting: 1 });
    expect(peopleCounts(s.network)).toEqual({ drafted: 2, waiting: 1 });
    expect(countsText({ drafted: 2, waiting: 1 })).toBe("2 drafted · 1 waiting on you");
  });
});

describe("outreach words", () => {
  it("the four limits have plain labels and the server maxes", () => {
    expect(OUTREACH_LIMITS.map((l) => [l.key, l.title, l.max])).toEqual([
      ["outreach_per_company", "People per company you applied to", 10],
      ["network_per_hour", "New network people per hour", 20],
      ["linkedin_sends_per_day", "LinkedIn sends per day", 100],
      ["email_sends_per_day", "Emails per day", 200],
    ]);
    expect(LINKEDIN_LIMIT_NOTE).toBe(
      "LinkedIn restricts accounts that send too many connection requests; about 100 a week is a common limit.",
    );
  });
  it("people hunt help says up to 5", () => {
    expect(PEOPLE_HUNT_HELP).toContain("up to 5 people for each new application");
  });
  it("the pace line reads the user's own limit", () => {
    expect(paceText(20)).toContain("at most 20 LinkedIn sends a day");
    expect(paceText(null)).not.toMatch(/at most 10/);
  });
});
