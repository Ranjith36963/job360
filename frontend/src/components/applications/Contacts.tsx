"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { addContact } from "@/lib/api";
import type { Contact } from "@/lib/api";
import { apiErrorMessage } from "@/lib/api-error";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";

const EMPTY_FORM = { name: "", role: "", email: "", linkedin_url: "", notes: "" };
// One stable empty list. An inline `= []` default would be a NEW array every
// render, the `[contacts]` effect below would fire every render, and setList
// would re-render forever.
const NO_CONTACTS: Contact[] = [];

/** S5: `linkedin_url` is rendered as text everywhere, an `<a href>` ONLY when
 * it actually starts with `https://` — a stored `javascript:` (or bare
 * `http://`) value must never become a clickable script. */
function LinkedinCell({ url }: { url: string }) {
  if (!url) return null;
  if (url.startsWith("https://")) {
    return (
      <a
        href={url}
        target="_blank"
        rel="noreferrer"
        className="text-primary hover:underline"
      >
        LinkedIn
      </a>
    );
  }
  return <span className="break-all">{url}</span>;
}

/** The People section on an application's detail page (spec R4): everyone the
 * agent (or the seeker) has attached to this application, plus a small form
 * to add one. Contacts are add-only (R2/S12) — there is no edit or delete
 * here, by design. */
export function Contacts({
  applicationId,
  contacts = NO_CONTACTS,
}: {
  applicationId: number;
  /** Optional on purpose: a detail payload without `contacts` (older backend,
   * a cached response, a test double) must render "no people yet", never
   * take the whole record page down with `undefined.length`. */
  contacts?: Contact[];
}) {
  // Own copy so a successful add can append instantly without waiting for a
  // full `GET /applications/{id}` round trip. Re-synced whenever the parent
  // hands down a freshly loaded list (e.g. after "Mark Applied" reloads).
  const [list, setList] = useState<Contact[]>(contacts);
  useEffect(() => {
    setList(contacts);
  }, [contacts]);

  const [form, setForm] = useState(EMPTY_FORM);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const setField = useCallback(
    (field: keyof typeof EMPTY_FORM) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
      setForm((prev) => ({ ...prev, [field]: e.target.value }));
    },
    []
  );

  const submit = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      const name = form.name.trim();
      if (!name) return;
      setFormError(null);
      setNote(null);
      setSubmitting(true);
      try {
        const body: {
          name: string;
          role?: string;
          email?: string;
          linkedin_url?: string;
          notes?: string;
        } = { name };
        if (form.role.trim()) body.role = form.role.trim();
        if (form.email.trim()) body.email = form.email.trim();
        if (form.linkedin_url.trim()) body.linkedin_url = form.linkedin_url.trim();
        if (form.notes.trim()) body.notes = form.notes.trim();

        const result = await addContact(applicationId, body);
        setList((prev) =>
          prev.some((c) => c.id === result.contact.id) ? prev : [...prev, result.contact]
        );
        if (result.already_existed) {
          setNote("This person is already on this application.");
        } else {
          setForm(EMPTY_FORM);
        }
      } catch (err) {
        setFormError(apiErrorMessage(err, "Could not add this person."));
      } finally {
        setSubmitting(false);
      }
    },
    [applicationId, form]
  );

  return (
    <div className="flex flex-col gap-4">
      {list.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No people yet. Your agent can add one with the MCP{" "}
          <code className="rounded bg-muted/40 px-1 py-0.5 text-xs">add_contact</code> tool, or
          use the form below.
        </p>
      ) : (
        <ul data-testid="contacts-list" className="flex flex-col gap-2">
          {list.map((contact) => (
            <li key={contact.id} className="glass-card rounded-lg p-3 text-sm">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className="font-medium">{contact.name}</span>
                {contact.role && (
                  <span className="text-muted-foreground">· {contact.role}</span>
                )}
              </div>
              {(contact.email || contact.linkedin_url) && (
                <div className="mt-1 flex flex-wrap gap-3 text-xs">
                  {contact.email && (
                    <a href={`mailto:${contact.email}`} className="text-primary hover:underline">
                      {contact.email}
                    </a>
                  )}
                  <LinkedinCell url={contact.linkedin_url} />
                </div>
              )}
              {contact.notes && (
                <p className="mt-1 text-xs text-foreground/80">{contact.notes}</p>
              )}
              <p className="mt-1 text-[11px] text-muted-foreground/70">
                added by {contact.added_by} ·{" "}
                {new Date(contact.created_at).toLocaleDateString()}
              </p>
            </li>
          ))}
        </ul>
      )}

      <form onSubmit={submit} className="glass-card flex flex-col gap-3 rounded-lg p-3">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Add a person
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="contact-name">Name</Label>
            <Input
              id="contact-name"
              value={form.name}
              onChange={setField("name")}
              placeholder="Jordan Lee"
              required
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="contact-role">Role</Label>
            <Input
              id="contact-role"
              value={form.role}
              onChange={setField("role")}
              placeholder="Recruiter"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="contact-email">Email</Label>
            <Input
              id="contact-email"
              type="email"
              value={form.email}
              onChange={setField("email")}
              placeholder="jordan@example.com"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="contact-linkedin">LinkedIn URL</Label>
            <Input
              id="contact-linkedin"
              value={form.linkedin_url}
              onChange={setField("linkedin_url")}
              placeholder="https://linkedin.com/in/…"
            />
          </div>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="contact-notes">Notes</Label>
          <Textarea
            id="contact-notes"
            value={form.notes}
            onChange={setField("notes")}
            placeholder="How you met, what they said…"
            rows={2}
          />
        </div>

        {formError && <p className="text-xs text-destructive">{formError}</p>}
        {note && <p className="text-xs text-muted-foreground">{note}</p>}

        <Button
          type="submit"
          size="sm"
          disabled={submitting || !form.name.trim()}
          className="self-start"
        >
          {submitting ? "Adding…" : "Add person"}
        </Button>
      </form>
    </div>
  );
}
