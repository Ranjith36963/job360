"use client";

import { useState } from "react";
import { contactSendBrowser, decideOwnBrowserSend, ownBrowserTask } from "@/lib/browser-contract";
import { BrowserReason } from "./BrowserReason";
import { JOB360_BROWSER_LABEL } from "@/lib/browser-labels";
import { closedSetLabel } from "@/lib/closed-sets";
import type { PersonToReach } from "@/lib/job-cards";
import { toast } from "@/lib/toast";
import { ApiError } from "@/lib/api-error";

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
): { state: string; text: string } {
  if (task.state === "in_progress") {
    const t = clock(task.claim_until);
    return { state: "sending", text: `Sending now${t ? ` \u2014 held until ${t}` : ""}.` };
  }
  if (kind === "own_browser") {
    return {
      state: "needs-own-browser",
      text: "Needs your own browser. Job360's browser couldn't send this one (see why below). Ask your assistant to send it from your own browser \u2014 you stay signed in to LinkedIn there.",
    };
  }
  if (kind === "human") {
    return { state: "needs-you", text: "Needs you. Open the profile and send it yourself, then press I sent it." };
  }
  const t = clock(task.approved_at);
  return {
    state: "waiting",
    text: `Waiting to send. You said yes${t ? ` at ${t}` : ""}. Your assistant sends it the next time it runs.`,
  };
}

const STATE_LABEL: Record<string, string> = {
  connected: "Connected",
  not_connected: "Not connected",
  pending: "Request pending",
  unknown: "LinkedIn state unknown",
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
  const where = c.found_via ? `Found via ${closedSetLabel(c.found_via)}` : "";
  const state = c.linkedin_state && c.linkedin_state in STATE_LABEL ? c.linkedin_state : "unknown";
  const isMessage = state === "connected";
  const count = person.message.length;
  const over = !isMessage && count > NOTE_LIMIT;
  // Field names are the Browser agent's contract: read only through the adapter.
  const task = ownBrowserTask(c);
  const sendBrowser = contactSendBrowser(c);
  // Which browser will send it: the backend's label, else Job360's browser (it goes first) while a Send is on offer.
  const browserText = sendBrowser?.label ?? (task ? JOB360_BROWSER_LABEL : null);
  const status = task && (task.state === "queued" || task.state === "in_progress")
    ? sendStatus(task, sendBrowser?.kind)
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
    <div className="flex flex-col gap-2 rounded-lg bg-muted/30 p-3" data-testid={`person-${c.id}`}>
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm font-medium">
          {c.name}
          {c.role && <span className="font-normal text-muted-foreground"> · {c.role}</span>}
        </p>
        <span className={chip} data-testid={`channel-${c.id}`}>{email ? "Email" : "LinkedIn"}</span>
        {browserText && (
          <span className={chip} data-testid={`send-browser-label-${c.id}`}>{browserText}</span>
        )}
        <span className={chip} data-testid={`li-state-${c.id}`}>{STATE_LABEL[state]}</span>
      </div>
      {sendBrowser && <BrowserReason label={sendBrowser} testid={`send-browser-reason-${c.id}`} />}
      {email && (
        <p className="text-xs text-muted-foreground" data-testid={`email-${c.id}`}>
          {email}{emailFrom ? ` · from ${closedSetLabel(emailFrom)}` : ""}
        </p>
      )}
      {where && <p className="text-xs text-muted-foreground">{where}</p>}
      <p className="font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint">
        {isMessage ? "Message" : "Connection note"}
      </p>
      <p className="whitespace-pre-wrap rounded-md border border-border bg-card p-2 text-sm">{person.message}</p>
      {!isMessage && (
        <div className="flex flex-col gap-0.5">
          <p className={`text-xs ${over ? "font-semibold text-danger" : "text-muted-foreground"}`}
            data-testid={`note-count-${c.id}`} data-over={over ? "true" : "false"}>
            {count}/{NOTE_LIMIT}
          </p>
          <p className="text-xs text-muted-foreground">LinkedIn connection notes are 200 characters at most</p>
        </div>
      )}
      {over && (
        <p className="text-xs text-danger" data-testid={`note-too-long-${c.id}`}>
          Too long for a connection note. Use it as a message after you connect, or rewrite it.
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        {(task?.state === "waiting_on_you" || task?.state === "declined") && !over && (
          <button type="button" className={primaryBtn} data-testid={`own-browser-send-${c.id}`}
            disabled={busy || sending}
            onClick={() => void decide("approve")}>
            Send
          </button>
        )}
        {status && task && (
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
        {task?.state === "queued" && (
          <button type="button" className={btn} data-testid={`own-browser-cancel-${c.id}`}
            disabled={busy || sending}
            onClick={() => void decide("decline")}>
            Don&apos;t send
          </button>
        )}
        {!over && (
        <button type="button" className={btn}
          onClick={() => navigator.clipboard.writeText(person.message).then(() => setCopied(true), () => toast.error("Couldn't copy"))}>
          {copied ? "Copied" : "Copy"}
        </button>
        )}
        <a href={person.linkedinUrl} target="_blank" rel="noopener noreferrer" className={btn}>
          Open profile ↗
        </a>
        <button type="button" className={btn} disabled={busy} data-testid={`sent-${c.id}`} onClick={onSent}>
          I sent it
        </button>
        <button type="button" className={btn} onClick={onSkip}>
          Skip
        </button>
      </div>
    </div>
  );
}
