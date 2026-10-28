"use client";

import { useState } from "react";
import { getPerson, markOutreachSent, markOutreachSkipped } from "@/lib/api";
import type { ListPeopleResult } from "@/lib/api";
import { PACE_TEXT } from "@/lib/browser-labels";
import { personToReach } from "@/lib/job-cards";
import type { PersonToReach } from "@/lib/job-cards";
import { toast } from "@/lib/toast";
import { PersonRow } from "./PersonRow";

// Names, roles and drafted notes are written by an assistant: untrusted, rendered only as React text.

type Person = NonNullable<ListPeopleResult["people"]>[number];

/** Full records read per page load, at most (one GET /api/people?contact_id= each). */
export const MAX_NEW_CONNECTIONS = 25;

/** People with no job (a cold contact: `application_id` null) whose newest drafted note is not sent yet.
 *  `people` is GET /api/people; each candidate row's full record (its note, its send state) is read on its
 *  own, and a failed read just leaves that person out. */
export async function newConnections(people: Person[]): Promise<PersonToReach[]> {
  const ids = people
    .filter((p) => p.jobs.length === 0 && p.message_count > 0 && !p.last_sent && !p.skipped)
    .flatMap((p) => p.contact_ids)
    .slice(0, MAX_NEW_CONNECTIONS);
  const rows = await Promise.all(ids.map((id) => getPerson(id).catch(() => null)));
  return rows.flatMap((c) => (c && c.application_id == null ? (personToReach(c, c.company ?? "") ?? []) : []));
}

/** "New connections" on Needs you (owner 2026-10-26): the same row and actions a person on a job card gets.
 *  `onDone` hides a person at once (sent or skipped, both saved first); nothing shows when the list is empty. */
export function NewConnections({
  people,
  onDone,
  onChanged,
}: {
  people: PersonToReach[];
  onDone: (contactId: number) => void;
  onChanged: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  if (people.length === 0) return null;

  async function sent(contactId: number) {
    setBusy(true);
    try {
      await markOutreachSent(contactId, "linkedin");
      onDone(contactId); // sent: handled, before the reload
      await onChanged();
    } catch (err) {
      toast.apiError(err, "Couldn't save that you sent it");
    } finally {
      setBusy(false);
    }
  }

  async function skip(contactId: number) {
    setBusy(true);
    try {
      await markOutreachSkipped(contactId);
      onDone(contactId); // skipped and saved: stays gone after a reload
    } catch (err) {
      toast.apiError(err, "Couldn't save the skip"); // the row stays
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      aria-labelledby="new-connections"
      data-testid="new-connections"
      className="flex flex-col gap-2 rounded-xl border border-border bg-card p-4 shadow-card"
    >
      <h2 id="new-connections" className="font-heading text-base">New connections</h2>
      <p className="text-xs text-muted-foreground">
        People your assistant added who are not linked to a job. Their notes are not sent yet.
      </p>
      <p className="text-xs text-muted-foreground">{PACE_TEXT}</p>
      {people.map((p) => (
        <PersonRow
          key={p.contact.id}
          person={p}
          busy={busy}
          onChanged={onChanged}
          onSent={() => void sent(p.contact.id)}
          onSkip={() => void skip(p.contact.id)}
        />
      ))}
    </section>
  );
}
