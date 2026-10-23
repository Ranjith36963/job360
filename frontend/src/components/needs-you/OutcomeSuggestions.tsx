"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { listOutcomeSuggestions, recordApplicationEvent, type OutcomeSuggestion } from "@/lib/api";
import { STATUS_LABEL } from "@/lib/event-labels";

const sectionLabel = "font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint";

/** "Suggested outcomes" on Needs you (feature 9). The assistant read an email and was not
 *  sure what it means; nothing changes until the user accepts. Accept records exactly the
 *  suggested event (as the web); Ignore records `outcome_dismissed`. The quote is untrusted
 *  email text: rendered as React text only. Best effort: a failed read shows nothing. */
export function OutcomeSuggestions() {
  const [items, setItems] = useState<OutcomeSuggestion[]>([]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (): Promise<OutcomeSuggestion[]> => {
    try {
      return (await listOutcomeSuggestions()).suggestions;
    } catch {
      return [];
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void load().then((r) => {
      if (!cancelled) setItems(r);
    });
    return () => {
      cancelled = true;
    };
  }, [load]);

  async function act(s: OutcomeSuggestion, kind: "accept" | "ignore") {
    setBusy(true);
    try {
      if (kind === "accept") {
        const e = s.email;
        await recordApplicationEvent(s.application_id, {
          event_type: s.event_type,
          occurred_at: s.occurred_at,
          source: {
            kind: e.kind,
            message_id: e.message_id,
            thread_id: e.thread_id ?? undefined,
            sender: e.sender,
            subject: e.subject,
            ...(e.received_at ? { received_at: e.received_at } : {}),
            quote: s.quote,
          },
        });
        toast.success("Recorded.");
      } else {
        await recordApplicationEvent(s.application_id, {
          event_type: "outcome_dismissed",
          payload: { suggestion_event_id: s.event_id },
        });
        toast.success("Ignored.");
      }
      setItems(await load());
    } catch {
      toast.error("Could not record that.");
    } finally {
      setBusy(false);
    }
  }

  if (items.length === 0) return null;
  return (
    <section
      aria-labelledby="outcome-suggestions-heading"
      data-testid="outcome-suggestions"
      className="flex flex-col gap-3 rounded-xl border border-border bg-card p-5 shadow-card"
    >
      <h2 id="outcome-suggestions-heading" className={sectionLabel}>
        Suggested outcomes
      </h2>
      <p className="text-sm text-muted-foreground">
        Your assistant read an email and was not sure what it means. Nothing changes until you accept.
      </p>
      <ul className="flex flex-col gap-3">
        {items.map((s) => (
          <li
            key={s.event_id}
            data-testid={`outcome-suggestion-${s.event_id}`}
            className="flex flex-col gap-1 rounded-xl border border-border p-4 text-sm"
          >
            <Link href={`/applications/${s.application_id}`} className="font-medium underline-offset-2 hover:underline">
              {[s.job_title, s.job_company].filter(Boolean).join(" · ")}
            </Link>
            <p data-testid="outcome-suggestion-type" className="font-medium">
              Suggested: {STATUS_LABEL[s.event_type] ?? s.event_type}
            </p>
            <blockquote
              data-testid="outcome-suggestion-quote"
              className="border-l-2 border-border pl-2 text-xs italic text-muted-foreground"
            >
              {`“${s.quote}”`}
            </blockquote>
            <p data-testid="outcome-suggestion-why" className="text-muted-foreground">
              Why unsure: {s.why}
            </p>
            <p data-testid="outcome-suggestion-email" className="text-xs text-muted-foreground">
              {`✉ ${s.email.sender} — “${s.email.subject}”`}
            </p>
            <div className="mt-1 flex gap-2">
              <Button size="sm" data-testid="outcome-suggestion-accept" disabled={busy} onClick={() => void act(s, "accept")}>
                Accept
              </Button>
              <Button
                size="sm"
                variant="outline"
                data-testid="outcome-suggestion-ignore"
                disabled={busy}
                onClick={() => void act(s, "ignore")}
              >
                Ignore
              </Button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
