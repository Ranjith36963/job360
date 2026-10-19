"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { getProfile, keepProfileEdit, takeBackProfileEdit, uploadProfile } from "@/lib/api";
import type { ProfileResponse } from "@/lib/types";
import { findAgentEdit } from "@/lib/agent-edits";
import { prefsFromRaw } from "@/components/profile/PreferencesForm";
import { AssistantNotes } from "@/components/profile/AssistantNotes";
import { EditedMark } from "@/components/profile/EditedMark";

// ---------------------------------------------------------------------------
// "Things your assistant should know" on Home — the standing lines the agent
// reads first on every get_profile, shown where the owner lands so they can
// be read and changed without opening Profile. Same data, same save path and
// same Keep / Take back mark as the Profile page; nothing new is stored.
//
// A user with no profile yet (or a failed read) sees nothing here: the card
// is a convenience, never a reason to block Home.
// ---------------------------------------------------------------------------

export function HomeAssistantNotes() {
  const [profile, setProfile] = useState<ProfileResponse | null>(null);
  const [saveFailed, setSaveFailed] = useState(false);
  // One save in flight at a time, in the order the edits were made: two
  // parallel requests could land out of order and leave the server holding an
  // older list than the screen. A queued save that a newer edit has already
  // replaced is skipped — the newer one carries the whole list.
  const seq = useRef(0);
  const chain = useRef<Promise<void>>(Promise.resolve());
  const latest = useRef<ProfileResponse | null>(null);

  useEffect(() => {
    let live = true;
    getProfile()
      .then((p) => {
        if (!live) return;
        latest.current = p;
        setProfile(p);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);

  const save = useCallback((notes: string[]) => {
    const base = latest.current;
    if (!base) return;
    const mine = ++seq.current;
    // Show the edit at once; the server's answer replaces it below.
    const shown = { ...base, preferences: { ...base.preferences, assistant_notes: notes } };
    latest.current = shown;
    setProfile(shown);
    chain.current = chain.current.then(async () => {
      if (mine !== seq.current) return;
      // The save replaces the whole preferences block, so send every other
      // field back exactly as stored — only the notes change.
      const prefs = { ...prefsFromRaw(shown.preferences ?? {}), assistant_notes: notes };
      try {
        const next = await uploadProfile(null, prefs);
        if (mine === seq.current) {
          latest.current = next;
          setProfile(next);
          setSaveFailed(false);
        }
      } catch {
        if (mine === seq.current) {
          setSaveFailed(true);
          toast.error("Couldn't save the note");
        }
      }
    });
  }, []);

  const settle = (run: (path: string) => Promise<ProfileResponse>, done: string, failed: string) =>
    async (path: string) => {
      try {
        const next = await run(path);
        latest.current = next;
        setProfile(next);
        toast.success(done);
      } catch {
        toast.error(failed);
      }
    };

  if (!profile) return null;

  const notes = prefsFromRaw(profile.preferences ?? {}).assistant_notes ?? [];
  const edit = findAgentEdit(profile.agent_edits ?? [], "preferences.assistant_notes");

  return (
    <section data-testid="home-assistant-notes" className="rounded-xl border border-border bg-card p-4">
      <AssistantNotes
        notes={notes}
        onChange={save}
        showHistory
        trailing={
          <EditedMark
            edit={edit}
            onTakeBack={settle(takeBackProfileEdit, "Change taken back", "Failed to take back the change")}
            onKeep={settle(keepProfileEdit, "Change kept", "Failed to keep the change")}
          />
        }
        footer={
          saveFailed ? (
            <p role="alert" className="text-xs text-destructive">
              Couldn&apos;t save — your next edit will retry
            </p>
          ) : undefined
        }
      />
    </section>
  );
}
