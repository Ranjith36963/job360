"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { Users } from "lucide-react";
import { toast as sonnerToast } from "sonner";
import { addPerson, getPerson, listPeople } from "@/lib/api";
import type { Person, PersonFull } from "@/lib/api";
import { apiErrorMessage } from "@/lib/api-error";
import { toast } from "@/lib/toast";
import { closedSetLabel } from "@/lib/closed-sets";
import { formatDate } from "@/lib/format-date";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/ui/empty-state";
import { FoundViaSelect } from "@/components/applications/FoundViaSelect";
import { LinkedinCell, addedByLabel } from "@/components/applications/Contacts";

const EMPTY_FORM = { name: "", role: "", email: "", linkedin_url: "", notes: "", found_via: "" };

// Same mapping as Contacts.tsx (not exported there).
const CHANNEL_LABEL: Record<string, string> = { linkedin: "LinkedIn", email: "email", other: "" };

const FIELD_LABEL: [string, string][] = [
  ["name", "Name"],
  ["role", "Role"],
  ["email", "Email"],
  ["linkedin_url", "LinkedIn URL"],
  ["notes", "Notes"],
  ["found_via", "Found via"],
];

function PersonHistory({ details }: { details: PersonFull[] }) {
  const multi = details.length > 1;
  const sections = details.map((d) => {
    const items: { key: string; text: string }[] = [];
    for (const [field, label] of FIELD_LABEL) {
      const entries = d.edit_history?.[field] ?? [];
      entries.slice(0, -1).forEach((e, i) => {
        const shown = e.value ? (field === "found_via" ? closedSetLabel(e.value) : e.value) : "(empty)";
        items.push({
          key: `${field}-${i}`,
          text: `${label}: was “${shown}” (${formatDate(e.recorded_at)}, by ${addedByLabel(e.recorded_by)})`,
        });
      });
    }
    const job = d.jobs?.[0];
    return { id: d.id, items, heading: job ? `${job.job_title} · ${job.job_company}` : "Not linked to a job" };
  });
  const any = sections.some((s) => s.items.length > 0);
  return (
    <div data-testid="person-history" className="mt-2 flex flex-col gap-2 text-xs text-muted-foreground">
      {!any && <p>No edits — these are the details as first saved.</p>}
      {sections.map((s) =>
        s.items.length === 0 && any ? null : (
          <div key={s.id}>
            {multi && <p className="font-medium text-foreground">{s.heading}</p>}
            {s.items.length > 0 && (
              <ul className="list-disc pl-4">
                {s.items.map((it) => (
                  <li key={it.key}>{it.text}</li>
                ))}
              </ul>
            )}
          </div>
        )
      )}
    </div>
  );
}

function PersonRow({ p }: { p: Person }) {
  const [open, setOpen] = useState(false);
  const [details, setDetails] = useState<PersonFull[] | null>(null);
  const [loading, setLoading] = useState(false);
  const inFlight = useRef(false);

  async function toggle() {
    if (open) {
      setOpen(false);
      return;
    }
    setOpen(true);
    if (details || inFlight.current) return;
    inFlight.current = true;
    setLoading(true);
    try {
      const res = await Promise.all(p.contact_ids.map((id) => getPerson(id)));
      setDetails(res.filter((r): r is PersonFull => r !== null));
    } catch (err) {
      toast.apiError(err, "Couldn't load this person's history.");
      setOpen(false);
    } finally {
      inFlight.current = false;
      setLoading(false);
    }
  }

  const via = p.last_sent ? (CHANNEL_LABEL[p.last_sent.channel] ?? p.last_sent.channel) : "";
  return (
    <li data-testid="person-row" className="rounded-md border border-border bg-card p-3 text-sm">
      <div className="flex flex-wrap items-baseline gap-1">
        <span className="font-medium">{p.name}</span>
        {p.role && <span className="text-muted-foreground">· {p.role}</span>}
      </div>
      {p.found_via && (
        <p data-testid="person-found-via" className="mt-1 text-xs text-muted-foreground">
          Found via {closedSetLabel(p.found_via)}
        </p>
      )}
      {(p.email || p.linkedin_url) && (
        <div className="mt-1 flex flex-wrap gap-3 text-xs">
          {p.email && (
            <a href={`mailto:${p.email}`} className="text-foreground underline underline-offset-2 hover:text-muted-foreground">
              {p.email}
            </a>
          )}
          <LinkedinCell url={p.linkedin_url} />
        </div>
      )}
      {p.notes && <p className="mt-1 text-xs text-muted-foreground">{p.notes}</p>}
      {p.jobs.length > 0 ? (
        <ul data-testid="person-jobs" className="mt-1 flex flex-col text-xs">
          {p.jobs.map((j) => (
            <li key={j.application_id}>
              <Link href={`/applications/${j.application_id}`} className="underline underline-offset-2">
                {j.job_title} · {j.job_company}
              </Link>
            </li>
          ))}
        </ul>
      ) : (
        <p data-testid="person-no-jobs" className="mt-1 text-xs text-muted-foreground">
          Not linked to a job
        </p>
      )}
      <div className="mt-1 flex flex-col gap-0.5 font-mono text-[11px] text-faint">
        {p.last_sent ? (
          <p data-testid="person-last-sent">
            Sent on {formatDate(p.last_sent.occurred_at)}
            {via && ` via ${via}`}
          </p>
        ) : (
          <span data-testid="person-not-sent">
            Not sent yet{p.message_count > 0 && ` · ${p.message_count} drafted`}
          </span>
        )}
        {p.replied && p.last_reply ? (
          <p data-testid="person-last-reply">Replied on {formatDate(p.last_reply.occurred_at)}</p>
        ) : (
          <p data-testid="person-no-reply">No reply yet</p>
        )}
      </div>
      <button
        type="button"
        data-testid="person-history-toggle"
        onClick={toggle}
        className="mt-1 text-[13px] text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
      >
        History
      </button>
      {open && loading && !details && (
        <p data-testid="person-history-loading" aria-busy="true" className="mt-1 text-xs text-muted-foreground">
          Loading history…
        </p>
      )}
      {open && details && <PersonHistory details={details} />}
    </li>
  );
}

export function PeopleClient() {
  const [rows, setRows] = useState<Person[] | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const load = useCallback(async (isCancelled: () => boolean = () => false) => {
    try {
      const res = await listPeople();
      if (isCancelled()) return;
      setRows(res.people ?? []);
      setTruncated(Boolean(res.truncated));
      setLoadFailed(false);
    } catch (err) {
      if (isCancelled()) return;
      setRows([]);
      setLoadFailed(true);
      toast.apiError(err, "Couldn't load your people.");
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- existing working effect; refactor tracked
    void load(() => cancelled);
    return () => {
      cancelled = true;
    };
  }, [load]);

  const setField =
    (k: keyof typeof EMPTY_FORM) => (e: { target: { value: string } }) =>
      setForm((prev) => ({ ...prev, [k]: e.target.value }));

  function reset() {
    setFormOpen(false);
    setFormError(null);
    setForm(EMPTY_FORM);
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!form.name.trim() || submitting) return;
    const body: Parameters<typeof addPerson>[0] = { name: form.name.trim() };
    if (form.role.trim()) body.role = form.role.trim();
    if (form.email.trim()) body.email = form.email.trim();
    if (form.linkedin_url.trim()) body.linkedin_url = form.linkedin_url.trim();
    if (form.notes.trim()) body.notes = form.notes.trim();
    if (form.found_via) body.found_via = form.found_via;
    setSubmitting(true);
    setFormError(null);
    try {
      const result = await addPerson(body);
      if (result.already_existed) sonnerToast("This person is already in your people.");
      reset();
      await load();
    } catch (err) {
      setFormError(apiErrorMessage(err, "Could not add this person."));
    } finally {
      setSubmitting(false);
    }
  }

  if (rows === null) {
    return (
      <div data-testid="people-loading" aria-busy="true" className="flex flex-col gap-2">
        <Skeleton className="h-16 w-full rounded-md" />
        <Skeleton className="h-16 w-full rounded-md" />
        <Skeleton className="h-16 w-full rounded-md" />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {loadFailed ? (
        <p data-testid="people-error" className="text-sm text-muted-foreground">
          Couldn&apos;t load your people. Reload to try again.
        </p>
      ) : rows.length === 0 ? (
        <div data-testid="people-empty">
          <EmptyState
            icon={<Users className="h-8 w-8" />}
            title="No people yet"
            description="When your assistant adds someone — a recruiter, a hiring manager, a referral — they show up here. You can add someone yourself too."
          />
        </div>
      ) : (
        <ul data-testid="people-list" className="flex flex-col gap-2">
          {rows.map((p) => (
            <PersonRow key={p.contact_ids.join("-")} p={p} />
          ))}
        </ul>
      )}

      {truncated && (
        <p data-testid="people-truncated" className="text-xs text-muted-foreground">
          Only your newest contacts are shown. Ask your assistant to look up anyone older by email.
        </p>
      )}

      {!formOpen ? (
        <button
          type="button"
          data-testid="people-add-toggle"
          onClick={() => setFormOpen(true)}
          className="self-start text-[13px] text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
        >
          + Add person
        </button>
      ) : (
        <form
          data-testid="people-add-form"
          onSubmit={submit}
          className="flex flex-col gap-3 border-t border-border pt-3"
        >
          <p className="font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint">
            Add a person
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="person-name">Name</Label>
              <Input id="person-name" value={form.name} onChange={setField("name")} placeholder="Jordan Lee" required />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="person-role">Role</Label>
              <Input id="person-role" value={form.role} onChange={setField("role")} placeholder="Recruiter" />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="person-email">Email</Label>
              <Input
                id="person-email"
                type="email"
                value={form.email}
                onChange={setField("email")}
                placeholder="jordan@example.com"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="person-linkedin">LinkedIn URL</Label>
              <Input
                id="person-linkedin"
                value={form.linkedin_url}
                onChange={setField("linkedin_url")}
                placeholder="https://linkedin.com/in/…"
              />
            </div>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="person-found-via">Found via (optional)</Label>
            <FoundViaSelect
              id="person-found-via"
              value={form.found_via}
              onChange={(v) => setForm((prev) => ({ ...prev, found_via: v }))}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="person-notes">Notes</Label>
            <Textarea
              id="person-notes"
              value={form.notes}
              onChange={setField("notes")}
              placeholder="How you met, what they said…"
              rows={2}
            />
          </div>

          {formError && <p className="text-xs text-destructive">{formError}</p>}

          <div className="flex gap-2">
            <Button
              type="submit"
              size="sm"
              disabled={submitting || !form.name.trim()}
              className="self-start bg-foreground text-background hover:bg-foreground/90"
            >
              {submitting ? "Adding…" : "Add person"}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={submitting}
              onClick={reset}
              className="self-start border-border bg-card"
            >
              Cancel
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}
