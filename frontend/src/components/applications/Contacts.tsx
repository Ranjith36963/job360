"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { toast } from "sonner";
import { addContact, updateContact } from "@/lib/api";
import type { Contact } from "@/lib/api";
import { apiErrorMessage } from "@/lib/api-error";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { formatDate } from "@/lib/format-date";

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

const CHANNEL_LABEL: Record<string, string> = { linkedin: "LinkedIn", email: "email", other: "" };

function channelLabel(channel: string): string {
  return CHANNEL_LABEL[channel] ?? channel;
}

/** "Sent on 3 Oct via LinkedIn" / "Not sent yet". */
function SentLine({ contact }: { contact: Contact }) {
  const last = contact.outreach?.last_sent;
  if (!last) return <p className="text-xs text-muted-foreground">Not sent yet.</p>;
  const via = channelLabel(last.channel);
  return (
    <p className="text-xs text-muted-foreground">
      Sent on {formatDate(last.occurred_at)}
      {via && ` via ${via}`}
    </p>
  );
}

/** "Replied on 5 Oct" / "No reply yet". */
function RepliedLine({ contact }: { contact: Contact }) {
  const last = contact.outreach?.last_reply;
  if (!last) return <p className="text-xs text-muted-foreground">No reply yet.</p>;
  return <p className="text-xs text-muted-foreground">Replied on {formatDate(last.occurred_at)}.</p>;
}

/** The latest message text, with earlier versions folded away — plain text
 * always, never HTML (a drafted message is untrusted free text). */
function MessageVersions({ contact }: { contact: Contact }) {
  const messages = contact.outreach?.messages ?? [];
  if (messages.length === 0) return null;
  const latest = messages[messages.length - 1];
  const earlier = messages.slice(0, -1);
  return (
    <div className="mt-1 text-xs">
      <p className="whitespace-pre-wrap text-foreground/90">{latest.text}</p>
      {earlier.length > 0 && (
        <details className="mt-1">
          <summary className="cursor-pointer text-muted-foreground">
            Earlier versions ({earlier.length})
          </summary>
          <ul className="mt-1 flex flex-col gap-1 border-l pl-2">
            {[...earlier].reverse().map((m) => (
              <li key={m.id} className="text-muted-foreground">
                <span className="text-[11px]">{formatDate(m.occurred_at)}</span>
                <p className="whitespace-pre-wrap">{m.text}</p>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

const EDITABLE_FIELDS = ["name", "role", "email", "notes"] as const;
type EditableField = (typeof EDITABLE_FIELDS)[number];
const FIELD_LABEL: Record<EditableField, string> = {
  name: "Name", role: "Role", email: "Email", notes: "Notes",
};

/** The "Edit" control (email/role/notes/name) — a PATCH appends history;
 * "was X" is folded so the common case (nothing edited yet) stays quiet. */
function EditContact({
  contact,
  onSaved,
}: {
  contact: Contact;
  onSaved: (updated: Contact) => void;
}) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({
    name: contact.name, role: contact.role, email: contact.email, notes: contact.notes,
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setForm({ name: contact.name, role: contact.role, email: contact.email, notes: contact.notes });
  }, [contact]);

  const save = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      setError(null);
      setSaving(true);
      try {
        const body: Record<string, string> = {};
        for (const field of EDITABLE_FIELDS) {
          if (form[field] !== contact[field]) body[field] = form[field];
        }
        if (Object.keys(body).length === 0) {
          setOpen(false);
          return;
        }
        const updated = await updateContact(contact.id, body);
        onSaved(updated);
        setOpen(false);
      } catch (err) {
        setError(apiErrorMessage(err, "Could not save this change."));
      } finally {
        setSaving(false);
      }
    },
    [contact, form, onSaved]
  );

  const history = contact.edit_history ?? {};
  const hasHistory = EDITABLE_FIELDS.some((f) => (history[f]?.length ?? 0) > 1);

  if (!open) {
    return (
      <div className="mt-1 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="text-[11px] font-medium text-primary hover:underline"
        >
          Edit
        </button>
        {hasHistory && (
          <details className="text-[11px] text-muted-foreground">
            <summary className="cursor-pointer">History</summary>
            <ul className="mt-1 flex flex-col gap-1 border-l pl-2">
              {EDITABLE_FIELDS.flatMap((field) => {
                const rows = history[field] ?? [];
                if (rows.length <= 1) return [];
                // Every value but the current one is "was X" — oldest first.
                return rows.slice(0, -1).map((row, i) => (
                  <li key={`${field}-${i}`}>
                    {FIELD_LABEL[field]}: was &ldquo;{row.value || "(empty)"}&rdquo; ({formatDate(row.recorded_at)})
                  </li>
                ));
              })}
            </ul>
          </details>
        )}
      </div>
    );
  }

  return (
    <form onSubmit={save} className="mt-2 flex flex-col gap-2 rounded border border-border/60 p-2">
      <div className="grid gap-2 sm:grid-cols-2">
        {EDITABLE_FIELDS.map((field) => (
          <div key={field} className="flex flex-col gap-1">
            <Label htmlFor={`edit-${field}-${contact.id}`} className="text-[11px]">
              {FIELD_LABEL[field]}
            </Label>
            <Input
              id={`edit-${field}-${contact.id}`}
              value={form[field]}
              onChange={(e) => setForm((prev) => ({ ...prev, [field]: e.target.value }))}
            />
          </div>
        ))}
      </div>
      {error && <p className="text-xs text-destructive">{error}</p>}
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </Button>
        <Button type="button" size="sm" variant="outline" disabled={saving} onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

/** The People section on an application's detail page (spec R4): everyone the
 * agent (or the seeker) has attached to this application, plus a small form
 * to add one, and a fold-out edit + outreach history per person (owner
 * decisions, 2026-09-25). The base contact row is still add-only (R2/S12);
 * an edit appends history instead of rewriting it. */
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

  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

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
          toast("This person is already on this application.");
        }
        setForm(EMPTY_FORM);
        setFormOpen(false);
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
          No people yet. Your assistant can add one, or use the button below.
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
                {formatDate(contact.created_at)}
              </p>
              <MessageVersions contact={contact} />
              <div className="mt-1 flex flex-col gap-0.5">
                <SentLine contact={contact} />
                <RepliedLine contact={contact} />
              </div>
              <EditContact
                contact={contact}
                onSaved={(updated) =>
                  setList((prev) => prev.map((c) => (c.id === updated.id ? updated : c)))
                }
              />
            </li>
          ))}
        </ul>
      )}

      {!formOpen ? (
        <button
          type="button"
          data-testid="contacts-add-toggle"
          onClick={() => setFormOpen(true)}
          className="self-start text-sm font-medium text-primary hover:underline"
        >
          + Add person
        </button>
      ) : (
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

        <div className="flex gap-2">
          <Button
            type="submit"
            size="sm"
            disabled={submitting || !form.name.trim()}
            className="self-start"
          >
            {submitting ? "Adding…" : "Add person"}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={submitting}
            onClick={() => {
              setFormOpen(false);
              setFormError(null);
              setForm(EMPTY_FORM);
            }}
            className="self-start"
          >
            Cancel
          </Button>
        </div>
      </form>
      )}
    </div>
  );
}
