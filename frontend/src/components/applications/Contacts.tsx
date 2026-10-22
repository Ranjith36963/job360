"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { toast } from "sonner";
import { addContact, confirmContact, updateContact } from "@/lib/api";
import type { ApplicationDetail, Contact } from "@/lib/api";
import { apiErrorMessage } from "@/lib/api-error";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { formatDate, formatDayMonth } from "@/lib/format-date";
import { closedSetLabel } from "@/lib/closed-sets";
import { FoundViaSelect } from "@/components/applications/FoundViaSelect";
import { RelationshipSelect } from "@/components/applications/RelationshipSelect";
import { CopyButton } from "@/components/applications/CopyButton";

/** "cv_data.cv_positions[0]: Northwind Ltd" -> "Northwind Ltd" (the pointer is for agents). */
export function sharedLabel(v: string): string {
  return v.replace(/^cv_data\.\w+\[\d+\]:\s*/, "");
}

const EMPTY_FORM = { name: "", role: "", email: "", linkedin_url: "", notes: "", found_via: "",
  relationship: "",
  phone: "",
  links: "",
};
// One stable empty list. An inline `= []` default would be a NEW array every
// render, the `[contacts]` effect below would fire every render, and setList
// would re-render forever.
const NO_CONTACTS: Contact[] = [];

type KnownPeople = NonNullable<ApplicationDetail["known_people"]>;

/** S5: `linkedin_url` is rendered as text everywhere, an `<a href>` ONLY when
 * it actually starts with `https://` — a stored `javascript:` (or bare
 * `http://`) value must never become a clickable script. */
export function LinkedinCell({ url }: { url: string }) {
  if (!url) return null;
  if (url.startsWith("https://")) {
    return (
      <a
        href={url}
        target="_blank"
        rel="noreferrer"
        className="text-foreground underline underline-offset-2 hover:text-muted-foreground"
      >
        LinkedIn
      </a>
    );
  }
  return <span className="break-all">{url}</span>;
}

/** An extra profile link: same rule as `LinkedinCell` — an `<a href>` ONLY for
 * `https://`; anything else (a stored `http://` or `javascript:`) is plain text. */
function LinkCell({ url }: { url: string }) {
  let label = url;
  try {
    label = new URL(url).hostname || url;
  } catch {
    label = url;
  }
  if (url.startsWith("https://")) {
    return (
      <a
        href={url}
        target="_blank"
        rel="noreferrer"
        data-testid="contact-link"
        className="text-foreground underline underline-offset-2 hover:text-muted-foreground"
      >
        {label}
      </a>
    );
  }
  return (
    <span data-testid="contact-link" className="break-all">
      {url}
    </span>
  );
}

/** One link per line -> a clean list (blank lines dropped). */
function parseLinks(text: string): string[] {
  return text.split("\n").map((l) => l.trim()).filter(Boolean);
}

const CHANNEL_LABEL: Record<string, string> = {
  linkedin: "LinkedIn",
  email: "email",
  other: "",
  call: "phone",
  video: "video",
  in_person: "in person",
};

const INTERACTION_LABEL: Record<string, string> = {
  meeting: "Meeting",
  call: "Call",
  intro: "Intro",
  referral_asked: "Asked for a referral",
  referral_given: "Referral given",
};

function channelLabel(channel: string): string {
  return CHANNEL_LABEL[channel] ?? channel;
}

/** Display mapping only — the stored `added_by` actor string (backend
 * `authorship.actor_for`: `"web"`, `"token:<name>"`, `"agent:<client>"`)
 * never changes; this is just plain words for it. `"web"` -> "you" (the
 * seeker added it in the browser); a token/agent actor -> its name, since
 * that is more useful here than the generic word "assistant". */
export function addedByLabel(addedBy: string): string {
  if (addedBy === "web") return "you";
  const idx = addedBy.indexOf(":");
  if (idx >= 0) return addedBy.slice(idx + 1).trim() || "your assistant";
  return addedBy;
}

/** "Sent on 3 Oct via LinkedIn" / "Not sent yet". */
function SentLine({ contact }: { contact: Contact }) {
  const last = contact.outreach?.last_sent;
  if (!last) return <p className="font-mono text-[11px] text-faint">Not sent yet.</p>;
  const via = channelLabel(last.channel);
  return (
    <p className="font-mono text-[11px] text-faint">
      Sent on {formatDate(last.occurred_at)}
      {via && ` via ${via}`}
    </p>
  );
}

/** "Replied on 5 Oct" / "No reply yet". */
function RepliedLine({ contact }: { contact: Contact }) {
  const last = contact.outreach?.last_reply;
  if (!last) return <p className="font-mono text-[11px] text-faint">No reply yet.</p>;
  return <p className="font-mono text-[11px] text-faint">Replied on {formatDate(last.occurred_at)}.</p>;
}

/** Meetings, calls, intros and referrals the user's assistant recorded: read-only lines,
 * notes as plain text (never HTML). */
function InteractionLines({ contact }: { contact: Contact }) {
  const rows = contact.outreach?.interactions ?? [];
  if (rows.length === 0) return null;
  return (
    <ul data-testid="contact-interactions" className="mt-1 flex flex-col gap-0.5 font-mono text-[11px] text-faint">
      {rows.map((r) => {
        const via = channelLabel(r.channel);
        return (
          <li key={r.id} data-testid="contact-interaction">
            {INTERACTION_LABEL[r.entry] ?? r.entry} on {formatDate(r.occurred_at)}
            {via && ` via ${via}`}
            {r.follow_up_on && ` · follow up ${formatDayMonth(r.follow_up_on)}`}
            {r.text && <span className="block whitespace-pre-wrap text-muted-foreground">{r.text}</span>}
          </li>
        );
      })}
    </ul>
  );
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
      <CopyButton getText={() => latest.text} testId="message-copy" className="mt-0.5" />
      {earlier.length > 0 && (
        <details className="mt-1">
          <summary className="cursor-pointer font-mono text-[11px] text-faint">
            Earlier versions ({earlier.length})
          </summary>
          <ul className="mt-1 flex flex-col gap-1 border-l border-border pl-2">
            {[...earlier].reverse().map((m) => (
              <li key={m.id} className="text-muted-foreground">
                <span className="text-[11px]">{formatDate(m.occurred_at)}</span>
                <p className="whitespace-pre-wrap">{m.text}</p>
                <CopyButton getText={() => m.text} testId="message-copy" />
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
  const [foundVia, setFoundVia] = useState(contact.found_via ?? "");
  const [relationship, setRelationship] = useState(contact.relationship ?? "");
  const [phone, setPhone] = useState(contact.phone ?? "");
  const [links, setLinks] = useState((contact.links ?? []).join("\n"));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- existing working effect; refactor tracked
    setForm({ name: contact.name, role: contact.role, email: contact.email, notes: contact.notes });
    setFoundVia(contact.found_via ?? "");
    setRelationship(contact.relationship ?? "");
    setPhone(contact.phone ?? "");
    setLinks((contact.links ?? []).join("\n"));
  }, [contact]);

  const save = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      setError(null);
      setSaving(true);
      try {
        const body: Record<string, string | string[] | null> = {};
        for (const field of EDITABLE_FIELDS) {
          if (form[field] !== contact[field]) body[field] = form[field];
        }
        // "Not set" clears it with "" (the backend reads null as "not given"),
        // never a default.
        if (foundVia !== (contact.found_via ?? "")) body.found_via = foundVia;
        if (relationship !== (contact.relationship ?? "")) body.relationship = relationship;
        if (phone.trim() !== (contact.phone ?? "")) body.phone = phone.trim();
        const nextLinks = parseLinks(links);
        if (nextLinks.join("\n") !== (contact.links ?? []).join("\n")) body.links = nextLinks;
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
    [contact, form, foundVia, relationship, phone, links, onSaved]
  );

  const history = contact.edit_history ?? {};
  const hasHistory = EDITABLE_FIELDS.some((f) => (history[f]?.length ?? 0) > 1);

  if (!open) {
    return (
      <div className="mt-1 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="font-mono text-[11px] text-faint underline-offset-2 hover:text-foreground hover:underline"
        >
          Edit
        </button>
        {hasHistory && (
          <details className="font-mono text-[11px] text-faint">
            <summary className="cursor-pointer">History</summary>
            <ul className="mt-1 flex flex-col gap-1 border-l border-border pl-2">
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
    <form onSubmit={save} className="mt-2 flex flex-col gap-2 rounded-lg border border-border p-3">
      <div className="grid gap-2 sm:grid-cols-2">
        {EDITABLE_FIELDS.map((field) => (
          <div key={field} className="flex flex-col gap-1">
            <Label htmlFor={`edit-${field}-${contact.id}`} className="font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint">
              {FIELD_LABEL[field]}
            </Label>
            <Input
              id={`edit-${field}-${contact.id}`}
              value={form[field]}
              onChange={(e) => setForm((prev) => ({ ...prev, [field]: e.target.value }))}
            />
          </div>
        ))}
        <div className="flex flex-col gap-1">
          <Label htmlFor={`edit-found-via-${contact.id}`} className="font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint">
            Found via
          </Label>
          <FoundViaSelect id={`edit-found-via-${contact.id}`} value={foundVia} onChange={setFoundVia} />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor={`edit-relationship-${contact.id}`} className="font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint">
            Relationship
          </Label>
          <RelationshipSelect
            id={`edit-relationship-${contact.id}`}
            value={relationship}
            onChange={setRelationship}
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor={`edit-phone-${contact.id}`} className="font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint">
            Phone
          </Label>
          <Input id={`edit-phone-${contact.id}`} value={phone} onChange={(e) => setPhone(e.target.value)} />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor={`edit-links-${contact.id}`} className="font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint">
            Other links
          </Label>
          <Textarea id={`edit-links-${contact.id}`} value={links} onChange={(e) => setLinks(e.target.value)} rows={2} />
        </div>
      </div>
      {error && <p className="text-xs text-destructive">{error}</p>}
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={saving} className="bg-foreground text-background hover:bg-foreground/90">
          {saving ? "Saving…" : "Save"}
        </Button>
        <Button type="button" size="sm" variant="outline" disabled={saving} onClick={() => setOpen(false)} className="border-border bg-card">
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
  knownPeople,
}: {
  applicationId: number;
  /** People the user already stored at this job's company (a display of stored
   * records, never a lookup). Nothing renders at 0 or when absent. */
  knownPeople?: KnownPeople;
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
    // eslint-disable-next-line react-hooks/set-state-in-effect -- existing working effect; refactor tracked
    setList(contacts);
  }, [contacts]);

  const [confirming, setConfirming] = useState<number | null>(null);
  const confirm = useCallback(async (id: number) => {
    setConfirming(id);
    try {
      const updated = await confirmContact(id);
      setList((prev) => prev.map((c) => (c.id === updated.id ? updated : c)));
    } catch (err) {
      toast.error(apiErrorMessage(err, "Could not confirm this person."));
    } finally {
      setConfirming(null);
    }
  }, []);

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
          found_via?: string;
          relationship?: string;
          phone?: string;
          links?: string[];
        } = { name };
        if (form.found_via) body.found_via = form.found_via;
        if (form.relationship) body.relationship = form.relationship;
        if (form.role.trim()) body.role = form.role.trim();
        if (form.email.trim()) body.email = form.email.trim();
        if (form.linkedin_url.trim()) body.linkedin_url = form.linkedin_url.trim();
        if (form.notes.trim()) body.notes = form.notes.trim();
        if (form.phone.trim()) body.phone = form.phone.trim();
        const links = parseLinks(form.links);
        if (links.length) body.links = links;

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
      {knownPeople && knownPeople.count > 0 && (
        <div>
          <p data-testid="known-people-count" className="text-sm">
            {knownPeople.count === 1
              ? "You already know 1 person here"
              : `You already know ${knownPeople.count} people here`}
          </p>
          <ul data-testid="known-people-list" className="mt-1 font-mono text-[11px] text-faint">
            {(knownPeople.people ?? []).map((p) => (
              <li key={p.contact_ids[0]}>
                {p.name}
                {p.role ? ` · ${p.role}` : ""} ·{" "}
                {p.last_sent_at ? `Sent ${formatDate(p.last_sent_at)}` : "Not contacted yet"}
                {p.replied ? " · replied" : ""}
              </li>
            ))}
          </ul>
        </div>
      )}
      {list.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No people yet. Your assistant can add one, or use the button below.
        </p>
      ) : (
        <ul data-testid="contacts-list" className="flex flex-col">
          {list.map((contact) => (
            <li key={contact.id} className="border-t border-border py-3 text-sm first:border-t-0 first:pt-0">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span>{contact.name}</span>
                {contact.role && (
                  <span className="text-muted-foreground">· {contact.role}</span>
                )}
              </div>
              {contact.found_via && (
                <p data-testid="contact-found-via" className="mt-1 font-mono text-[11px] text-faint">
                  Found via {closedSetLabel(contact.found_via)}
                </p>
              )}
              {contact.relationship && (
                <p data-testid="contact-relationship" className="mt-1 font-mono text-[11px] text-faint">
                  {closedSetLabel(contact.relationship)}
                </p>
              )}
              {contact.shared && (
                <p data-testid="contact-shared" className="mt-1 font-mono text-[11px] text-faint">
                  Shared: {sharedLabel(contact.shared)}
                </p>
              )}
              {(contact.email || contact.linkedin_url || contact.phone || (contact.links ?? []).length > 0) && (
                <div className="mt-1 flex flex-wrap gap-3 text-xs">
                  {contact.email && (
                    <a href={`mailto:${contact.email}`} className="text-foreground underline underline-offset-2 hover:text-muted-foreground">
                      {contact.email}
                    </a>
                  )}
                  <LinkedinCell url={contact.linkedin_url} />
                  {contact.phone && (
                    <a
                      data-testid="contact-phone"
                      href={`tel:${contact.phone}`}
                      className="text-foreground underline underline-offset-2 hover:text-muted-foreground"
                    >
                      {contact.phone}
                    </a>
                  )}
                  {(contact.links ?? []).map((url) => (
                    <LinkCell key={url} url={url} />
                  ))}
                </div>
              )}
              {contact.notes && (
                <p className="mt-1 text-xs text-muted-foreground">{contact.notes}</p>
              )}
              <p className="mt-1 font-mono text-[11px] text-faint">
                added by {addedByLabel(contact.added_by)} ·{" "}
                {formatDate(contact.created_at)}
              </p>
              {contact.confirmed === false && (
                <div className="mt-0.5 flex flex-wrap items-center gap-2">
                  <span data-testid="contact-unconfirmed" className="font-mono text-[11px] text-faint">
                    Not confirmed yet
                  </span>
                  <button
                    type="button"
                    data-testid="contact-confirm"
                    disabled={confirming === contact.id}
                    onClick={() => void confirm(contact.id)}
                    className="font-mono text-[11px] text-faint underline-offset-2 hover:text-foreground hover:underline disabled:opacity-50"
                  >
                    Confirm
                  </button>
                </div>
              )}
              <MessageVersions contact={contact} />
              <div className="mt-1 flex flex-col gap-0.5">
                <SentLine contact={contact} />
                <RepliedLine contact={contact} />
              </div>
              <InteractionLines contact={contact} />
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
          className="self-start text-[13px] text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
        >
          + Add person
        </button>
      ) : (
      <form onSubmit={submit} className="flex flex-col gap-3 border-t border-border pt-3">
        <p className="font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint">
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
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="contact-phone">Phone</Label>
            <Input
              id="contact-phone"
              value={form.phone}
              onChange={setField("phone")}
              placeholder="+44 7700 900123"
            />
          </div>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="contact-links">Other links (one per line, up to 5)</Label>
          <Textarea
            id="contact-links"
            value={form.links}
            onChange={setField("links")}
            rows={2}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="contact-found-via">Found via (optional)</Label>
          <FoundViaSelect
            id="contact-found-via"
            value={form.found_via}
            onChange={(v) => setForm((prev) => ({ ...prev, found_via: v }))}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="contact-relationship">Relationship (optional)</Label>
          <RelationshipSelect
            id="contact-relationship"
            value={form.relationship}
            onChange={(v) => setForm((prev) => ({ ...prev, relationship: v }))}
          />
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
            className="self-start bg-foreground text-background hover:bg-foreground/90"
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
