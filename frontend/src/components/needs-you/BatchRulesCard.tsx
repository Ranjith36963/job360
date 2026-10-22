"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { getProfile, getAssistantSettings, updateProfileFields } from "@/lib/api";
import type { AssistantSettingsView } from "@/lib/api";
import { BatchRuleRows } from "@/components/profile/BatchRuleRows";

const LABEL = "font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint";

/** Your own batch rules, edited in place. Uses the same save as Profile -> Assistant:
 *  a PATCH as the web user that applies at once (your click is the confirmation).
 *  Empty = not set; nothing is ever written for you. */
/** Fired after a rule is saved, so the queue card refreshes its "left today" counts. */
export const BATCH_RULES_SAVED = "job360:batch-rules-saved";

export function BatchRulesCard() {
  const [view, setView] = useState<AssistantSettingsView | null>(null);
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState(false);
  const [avoid, setAvoid] = useState<string[] | null>(null);

  const load = useCallback(async () => {
    try {
      setView(await getAssistantSettings());
      setError(false);
    } catch {
      setError(true);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- existing working effect; refactor tracked
    void load();
    (async () => {
      try {
        const raw = (await getProfile()).preferences?.avoid_companies;
        setAvoid(Array.isArray(raw) ? raw.map(String).filter(Boolean) : []);
      } catch {
        setAvoid(null);
      }
    })();
  }, [load]);

  async function saveMany(changes: { path: string; value: unknown }[]) {
    setBusy(true);
    try {
      await updateProfileFields(changes);
      toast.success("Saved.");
      // The queue's "left today" line reads these limits: tell it to reload (real-browser check, 2026-10-18).
      window.dispatchEvent(new Event(BATCH_RULES_SAVED));
    } catch {
      toast.error("Could not save. Try again.");
    } finally {
      await load();
      setBusy(false);
    }
  }
  const save = (path: string, value: unknown) => saveMany([{ path, value }]);

  return (
    <section
      aria-labelledby="batch-rules"
      data-testid="batch-rules"
      className="flex flex-col gap-1 rounded-xl border border-border bg-card p-5 shadow-card"
    >
      <h3 id="batch-rules" className={LABEL}>
        Your rules
      </h3>
      <p className="pb-1 text-[12.5px] text-muted-foreground">
        Leave a box empty and that rule is not set. A change counts the moment you make it.
      </p>
      {error && !view ? (
        <div role="alert" className="flex items-center gap-3 py-2 text-sm text-destructive">
          <span>Could not load your rules.</span>
          <button
            type="button"
            data-testid="batch-rules-retry"
            onClick={() => void load()}
            className="text-xs font-medium underline-offset-2 hover:underline"
          >
            Try again
          </button>
        </div>
      ) : !view ? (
        <p className="py-2 text-sm text-muted-foreground" data-testid="batch-rules-loading">
          Loading…
        </p>
      ) : (
        <>
          <BatchRuleRows view={view} busy={busy} save={save} saveMany={saveMany} />
          <div data-testid="batch-never-apply" className="border-t border-border pt-3 text-[13px]">
            <div className="font-medium">Never apply to</div>
            <p className="text-[12.5px] text-muted-foreground">
              {avoid === null
                ? "Could not read your never-apply list."
                : avoid.length === 0
                  ? "No companies on your list."
                  : avoid.join(", ")}
            </p>
            <p className="mt-1 text-[12.5px]">
              <Link
                href="/profile"
                data-testid="batch-never-apply-link"
                className="text-muted-foreground underline underline-offset-2 hover:text-foreground"
              >
                Edit in Profile
              </Link>
            </p>
          </div>
        </>
      )}
    </section>
  );
}
