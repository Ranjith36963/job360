"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { getProfile, keepProfileEdit, takeBackProfileEdit, updateProfileFields } from "@/lib/api";
import type { ProfileResponse } from "@/lib/types";
import { findAgentEdit } from "@/lib/agent-edits";
import { isStepFact } from "@/lib/step-facts";
import { AssistantNotes } from "@/components/profile/AssistantNotes";
import { EditedMark } from "@/components/profile/EditedMark";

// ---------------------------------------------------------------------------
// Standing instructions for the assistant, on Connect your assistant > Assistant
// configure (owner rule, 2026-10-26: every assistant setting lives on /connect).
// "Things your AI assistant should know" = the lines the agent reads first on
// every get_profile, with the same Keep / Take back mark as before. "Never apply
// to" = the companies the assistant skips. Each save writes ONE preference path,
// so the two editors can never overwrite each other.
//
// A user with no profile yet (or a failed read) sees nothing here.
// ---------------------------------------------------------------------------

function NotesCard() {
  const [profile, setProfile] = useState<ProfileResponse | null>(null);
  const [saveFailed, setSaveFailed] = useState(false);
  const [settling, setSettling] = useState(false);
  // One request in flight at a time, in the order the owner acted: two
  // parallel requests could land out of order and leave the server holding an
  // older list than the screen. A queued save that a newer edit has already
  // replaced is skipped — the newer one carries the whole list. Keep and Take
  // back join the same queue, so a note save still waiting can never land after
  // them and undo them; and the notes are locked until they answer, so no edit
  // can start from the list they are about to change.
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
      try {
        const next = await updateProfileFields([{ path: "preferences.assistant_notes", value: notes }]);
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
    (path: string) => {
      setSettling(true);
      // eslint-disable-next-line react-hooks/refs -- existing working ref read; refactor tracked
      const step = chain.current.then(async () => {
        try {
          const next = await run(path);
          // eslint-disable-next-line react-hooks/refs -- existing working ref read; refactor tracked
          latest.current = next;
          setProfile(next);
          toast.success(done);
        } catch {
          toast.error(failed);
        } finally {
          setSettling(false);
        }
      });
      chain.current = step;
      return step;
    };

  if (!profile) return null;

  const stored = profile.preferences?.assistant_notes;
  const all = Array.isArray(stored) ? stored.map(String) : [];
  // General instructions only: a fact filed under a Memory step ("About you: …") is
  // shown in that step. Every save sends them back unchanged, after the general ones.
  const notes = all.filter((n) => !isStepFact(n));
  const stepFacts = all.filter(isStepFact);
  const edit = findAgentEdit(profile.agent_edits ?? [], "preferences.assistant_notes");

  return (
    <section data-testid="assistant-notes-card" className="rounded-xl border border-border bg-card p-4">
      <AssistantNotes
        notes={notes}
        onChange={(next) => save([...next, ...stepFacts])}
        disabled={settling}
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

export function AssistantInstructions() {
  return (
    <div className="flex flex-col gap-6" data-testid="assistant-instructions">
      <NotesCard />
    </div>
  );
}
