"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { listApplications, listInboxItems, resolveInboxItem } from "@/lib/api";
import type { InboxItem } from "@/lib/api";
import { relativeTime } from "@/lib/utils";

const sectionLabel = "font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint";

type AppOption = { id: number; label: string };

/** Emails your assistant read but could not tie to one application. The user picks the
 *  application or dismisses. Picking stores the choice only; the assistant records the event.
 *  Best effort: a failed read or no items shows nothing. Email text is untrusted: text nodes only. */
export function UnmatchedMail() {
  const [items, setItems] = useState<InboxItem[]>([]);
  const [apps, setApps] = useState<AppOption[]>([]);
  const [picked, setPicked] = useState<Record<number, string>>({});

  const fetchAll = useCallback(async () => {
    try {
      const [inbox, list] = await Promise.all([listInboxItems("open"), listApplications({ limit: 200 })]);
      const options = list.applications.map((a) => ({ id: a.id, label: [a.job_title, a.job_company].filter(Boolean).join(" · ") }));
      return { items: inbox.items, apps: options };
    } catch {
      return { items: [] as InboxItem[], apps: [] as AppOption[] };
    }
  }, []);
  const load = useCallback(async () => {
    const r = await fetchAll();
    setItems(r.items);
    setApps(r.apps);
  }, [fetchAll]);

  useEffect(() => {
    let cancelled = false;
    void fetchAll().then((r) => {
      if (cancelled) return;
      setItems(r.items);
      setApps(r.apps);
    });
    return () => {
      cancelled = true;
    };
  }, [fetchAll]);

  const resolve = async (id: number, body: { application_id: number } | { dismiss: true }, ok?: string) => {
    try {
      await resolveInboxItem(id, body);
      if (ok) toast.success(ok);
      await load();
    } catch {
      toast.error("Could not save that. Try again.");
    }
  };

  if (items.length === 0) return null;
  return (
    <section
      aria-labelledby="unmatched-mail"
      data-testid="unmatched-mail"
      className="flex flex-col gap-3 rounded-xl border border-border bg-card p-5 shadow-card"
    >
      <h2 id="unmatched-mail" className={sectionLabel}>
        Emails your assistant could not match
      </h2>
      <ul className="flex flex-col gap-3">
        {items.map((it) => {
          const choice = picked[it.id] ?? "";
          return (
            <li
              key={it.id}
              data-testid={`inbox-item-${it.id}`}
              className="flex flex-col gap-2 rounded-xl border border-border p-4 text-sm"
            >
              <p className="font-medium">{it.subject || "(no subject)"}</p>
              <p className="text-muted-foreground">
                {it.sender}
                <span className="font-mono"> {relativeTime(it.received_at ?? it.created_at)}</span>
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <select
                  data-testid={`inbox-item-app-${it.id}`}
                  aria-label="Which application is this email about?"
                  value={choice}
                  onChange={(e) => setPicked((p) => ({ ...p, [it.id]: e.target.value }))}
                  className="rounded-md border border-border bg-background px-2 py-1"
                >
                  <option value="">Pick an application…</option>
                  {apps.map((a) => (
                    <option key={a.id} value={String(a.id)}>
                      {a.label}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  data-testid={`inbox-item-match-${it.id}`}
                  disabled={!choice}
                  onClick={() => void resolve(it.id, { application_id: Number(choice) }, "Matched. Your assistant records it on its next run.")}
                  className="rounded-md border border-border px-3 py-1 disabled:opacity-50"
                >
                  Match
                </button>
                <button
                  type="button"
                  data-testid={`inbox-item-dismiss-${it.id}`}
                  onClick={() => void resolve(it.id, { dismiss: true })}
                  className="rounded-md border border-border px-3 py-1"
                >
                  Dismiss
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
