"use client";

import { useState } from "react";
import { contactSendBrowser, decideOwnBrowserSend, ownBrowserTask } from "@/lib/browser-contract";
import { BrowserReason } from "./BrowserReason";
import { LINKEDIN_OWN_BROWSER_LABEL, LINKEDIN_OWN_BROWSER_REASON } from "@/lib/browser-labels";
import { closedSetLabel } from "@/lib/closed-sets";
import type { PersonToReach } from "@/lib/job-cards";
import { toast } from "@/lib/toast";
import { ApiError } from "@/lib/api-error";
import { linkedinProfileHref } from "@/lib/safe-link";

// Names, roles and drafted messages are written by an assistant or a job site: untrusted,
// rendered only as React text, never as HTML.

const btn =
  "rounded-md border border-border px-2.5 py-1 text-xs font-medium hover:bg-muted/50 disabled:opacity-50";
const primaryBtn =
  "rounded-md bg-primary px-2.5 py-1 text-xs font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50";
const chip = "rounded-full border border-border px-2 py-0.5 text-[11px] font-medium text-muted-foreground";

export const NOTE_LIMIT = 200;

function clock(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** The plain-words line for a queued or in-progress send. Text only, never HTML. */
function sendStatus(
  task: { state: string; approved_at: string | null; claim_until: string | null },
  kind: string | undefined,
  reason: string | undefined,
): { state: string; text: string } {
  if (task.state === "in_progress") {
    const t = clock(task.claim_until);
    return { state: "sending", text: `Sending now${t ? ` \u2014 held until ${t}` : ""}.` };
  }
  // LinkedIn goes only through the user's own browser, by their choice (owner 2026-10-26): waiting, not broken.
  if (kind === "own_browser" && reason === LINKEDIN_OWN_BROWSER_REASON) {
    return {
      state: "waiting-own-browser",
      text: "Goes out next time your assistant runs on your PC.",
    };
  }
  if (kind === "own_browser") {
    return {
      state: "needs-own-browser",
      text: "Your PC: needs your own browser.",
    };
  }
  if (kind === "human") {
    return { state: "needs-you", text: "You: send it from the profile, then press I sent it." };
  }
  const t = clock(task.approved_at);
  return {
    state: "waiting",
    text: `You said Send${t ? ` at ${t}` : ""}. Goes out next time your assistant runs.`,
  };
}

const STATE_LABEL: Record<string, string> = {
  connected: "Connected",
  not_connected: "Not connected",
  pending: "Request pending",
};


export function PersonRow({
  person,
  busy,
  onSent,
  onSkip,
  onChanged,
}: {
  person: PersonToReach;
  busy: boolean;
  onSent: () => void;
  onSkip: () => void;
  onChanged: () => Promise<void>;
}) {
  const [copied, setCopied] = useState(false);
  const [sending, setSending] = useState(false);
  const c = person.contact;
  const email = c.email?.trim() ?? "";
  const emailFrom = c.email_source ?? c.found_via;
  const where = c.found_via && c.found_via !== "other" ? `Found via ${closedSetLabel(c.found_via)}` : "";
  // A state chip only when the state is known; "unknown" is never shown.
  const state = c.linkedin_state && c.linkedin_state in STATE_LABEL ? c.linkedin_state : "";
  const profile = linkedinProfileHref(c.linkedin_url);
  // The assistant, not the user, shortens an over-limit note: the row shows one muted line and no actions.
  const rewrite = c.note_rewrite ?? null;
  const isMessage = state === "connected";
  const count = person.message.length;
  // Field names are the Browser agent's contract: read only through the adapter.
  const task = ownBrowserTask(c);
  const sendBrowser = contactSendBrowser(c);
  // Which browser will send it: the backend's label, else the user's own browser (LinkedIn goes only there)
  // while a Send is on offer.
  const browserText = sendBrowser?.label ?? (task ? LINKEDIN_OWN_BROWSER_LABEL : null);
  const status = task && (task.state === "queued" || task.state === "in_progress")
    ? sendStatus(task, sendBrowser?.kind, sendBrowser?.reason)
    : null;

  async function decide(decision: "approve" | "decline") {
    if (!task) return;
    setSending(true);
    try {
      await decideOwnBrowserSend(c.id, task.message_id, decision);
      await onChanged();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // The draft changed (or it was already sent): say so and show the current state.
        toast.error("The draft changed since you looked. Look again.");
        await onChanged().catch(() => {});
      } else toast.apiError(err, "Couldn't save that");
    } finally {
      setSending(false);
    }
  }

  return (
    <div id={`person-${c.id}`} className="flex flex-col gap-2 rounded-lg bg-muted/30 p-3" data-testid={`person-${c.id}`}>
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm font-medium">
          {profile ? (
            <a href={profile} target="_blank" rel="noopener noreferrer" data-testid={`name-link-${c.id}`}
              className="underline-offset-2 hover:underline">{c.name}</a>
          ) : c.name}
          {c.role && <span className="font-normal text-muted-foreground"> · {c.role}</span>}
        </p>
        {!email && profile ? (
          <a href={profile} target="_blank" rel="noopener noreferrer" className={chip} data-testid={`channel-${c.id}`}>LinkedIn</a>
        ) : (
          <span className={chip} data-testid={`channel-${c.id}`}>{email ? "Email" : "LinkedIn"}</span>
        )}
        {browserText && (
          <span className={chip} data-testid={`send-browser-label-${c.id}`}>{browserText}</span>
        )}
        {state && <span className={chip} data-testid={`li-state-${c.id}`}>{STATE_LABEL[state]}</span>}
      </div>
      {sendBrowser && <BrowserReason label={sendBrowser} testid={`send-browser-reason-${c.id}`} />}
      {email && (
        <p className="text-xs text-muted-foreground" data-testid={`email-${c.id}`}>
          {email}{emailFrom ? ` · from ${closedSetLabel(emailFrom)}` : ""}
        </p>
      )}
      {where && <p className="text-xs text-muted-foreground">{where}</p>}
      {rewrite && (
        <p className="text-xs text-muted-foreground" data-testid={`note-rewrite-${c.id}`}>
          Your assistant is rewriting this note: {rewrite.chars}/{rewrite.limit}
        </p>
      )}
      {!rewrite && <>
      <p className="font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint">
        {isMessage ? "Message" : "Connection note"}
      </p>
      <p className="whitespace-pre-wrap rounded-md border border-border bg-card p-2 text-sm">{person.message}</p>
      {!isMessage && (
        <div className="flex flex-col gap-0.5">
          <p className={`text-xs text-muted-foreground`}
            data-testid={`note-count-${c.id}`} data-over="false">
            {count}/{NOTE_LIMIT}
          </p>
          <p className="text-xs text-muted-foreground">LinkedIn connection notes are 200 characters at most</p>
        </div>
      )}
      </>}
      <div className="flex flex-wrap gap-2">
        {(task?.state === "waiting_on_you" || task?.state === "declined") && !rewrite && (
          <button type="button" className={primaryBtn} data-testid={`own-browser-send-${c.id}`}
            disabled={busy || sending}
            onClick={() => void decide("approve")}>
            Send
          </button>
        )}
        {status && task && !rewrite && (
          <div className="w-full" data-testid={`own-browser-${task.state === "queued" ? "queued" : "in-progress"}-${c.id}`}>
            <p
              className={`text-xs ${status.state.startsWith("needs") ? "text-warning" : "text-muted-foreground"}`}
              data-testid={`send-status-${c.id}`}
              data-state={status.state}
            >
              {status.text}
            </p>
          </div>
        )}
        {task?.state === "queued" && !rewrite && (
          <button type="button" className={btn} data-testid={`own-browser-cancel-${c.id}`}
            disabled={busy || sending}
            onClick={() => void decide("decline")}>
            Don&apos;t send
          </button>
        )}
        {!rewrite && (
        <>
        <button type="button" className={btn}
          onClick={() => navigator.clipboard.writeText(person.message).then(() => setCopied(true), () => toast.error("Couldn't copy"))}>
          {copied ? "Copied" : "Copy"}
        </button>
        <button type="button" className={btn} disabled={busy} data-testid={`sent-${c.id}`} onClick={onSent}>
          I sent it
        </button>
        </>
        )}
        <button type="button" className={btn} disabled={busy} data-testid={`skip-${c.id}`} onClick={onSkip}>
          Skip
        </button>
      </div>
    </div>
  );
}
