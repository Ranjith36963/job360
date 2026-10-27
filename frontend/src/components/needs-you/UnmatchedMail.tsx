"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { listApplications, listInboxItems, resolveInboxItem } from "@/lib/api";
import type { InboxItem } from "@/lib/api";
import { relativeTime } from "@/lib/utils";
import { onChanges } from "@/lib/live-changes";

export type AppOption = { id: number; label: string };

/** Emails your assistant read but could not tie to one application (rows of the Other list).
 *  Picking stores the choice only; the assistant records the event. Best effort: a failed read is none. */
export function useUnmatchedMail() {
  const [items, setItems] = useState<InboxItem[]>([]);
  const [apps, setApps] = useState<AppOption[]>([]);

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

  // Live (PR 6): re-read quietly when unmatched mail moved; a failed read keeps the rows.
  useEffect(() => onChanges((d) => {
    if (!d.full && !d.areas.includes("other")) return;
    Promise.all([listInboxItems("open"), listApplications({ limit: 200 })]).then(([inbox, list]) => {
      setItems(inbox.items);
      setApps(list.applications.map((a) => ({ id: a.id, label: [a.job_title, a.job_company].filter(Boolean).join(" · ") })));
    }, () => {});
  }), []);

  const resolve = async (id: number, body: { application_id: number } | { dismiss: true }, ok?: string) => {
    try {
      await resolveInboxItem(id, body);
      if (ok) toast.success(ok);
      await load();
    } catch {
      toast.error("Could not save that. Try again.");
    }
  };

  return { items, apps, resolve };
}

/** One email, opened from its Other row: pick the application or dismiss. Email text is untrusted: text nodes only. */
export function InboxItemCard({ it, apps, onResolve }: {
  it: InboxItem;
  apps: AppOption[];
  onResolve: (id: number, body: { application_id: number } | { dismiss: true }, ok?: string) => Promise<void>;
}) {
  const [choice, setChoice] = useState("");
  return (
    <div data-testid={`inbox-item-${it.id}`} className="flex flex-col gap-2 text-sm">
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
          onChange={(e) => setChoice(e.target.value)}
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
          onClick={() => void onResolve(it.id, { application_id: Number(choice) }, "Matched. Your assistant records it on its next run.")}
          className="rounded-md border border-border px-3 py-1 disabled:opacity-50"
        >
          Match
        </button>
        <button
          type="button"
          data-testid={`inbox-item-dismiss-${it.id}`}
          onClick={() => void onResolve(it.id, { dismiss: true })}
          className="rounded-md border border-border px-3 py-1"
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}
