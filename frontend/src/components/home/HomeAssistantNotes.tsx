"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { getProfile, keepProfileEdit, takeBackProfileEdit, uploadProfile } from "@/lib/api";
import type { ProfileResponse } from "@/lib/types";
import { findAgentEdit } from "@/lib/agent-edits";
import { AssistantNotes, prefsFromRaw } from "@/components/profile/PreferencesForm";
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
  // Latest-wins: a slow save must not overwrite the result of a newer edit.
  const seq = useRef(0);

  useEffect(() => {
    let live = true;
    getProfile()
      .then((p) => live && setProfile(p))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);

  const save = useCallback(
    async (notes: string[]) => {
      if (!profile) return;
      const mine = ++seq.current;
      // The save replaces the whole preferences block, so send every other
      // field back exactly as stored — only the notes change.
      const prefs = { ...prefsFromRaw(profile.preferences ?? {}), assistant_notes: notes };
      // Show the edit at once; the server's answer replaces it below.
      setProfile({ ...profile, preferences: { ...profile.preferences, assistant_notes: notes } });
      try {
        const next = await uploadProfile(null, prefs);
        if (mine === seq.current) {
          setProfile(next);
          setSaveFailed(false);
        }
      } catch {
        if (mine === seq.current) setSaveFailed(true);
        toast.error("Couldn't save the note");
      }
    },
    [profile]
  );

  const settle = (run: (path: string) => Promise<ProfileResponse>, done: string, failed: string) =>
    async (path: string) => {
      try {
        setProfile(await run(path));
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
