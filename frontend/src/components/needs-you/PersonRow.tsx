"use client";

import { useState } from "react";
import { browserSendState, queueBrowserSend } from "@/lib/browser-contract";
import { closedSetLabel } from "@/lib/closed-sets";
import type { PersonToReach } from "@/lib/job-cards";
import { toast } from "@/lib/toast";

// Names, roles and drafted messages are written by an assistant or a job site: untrusted,
// rendered only as React text, never as HTML.

const btn =
  "rounded-md border border-border px-2.5 py-1 text-xs font-medium hover:bg-muted/50 disabled:opacity-50";
const primaryBtn =
  "rounded-md bg-primary px-2.5 py-1 text-xs font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50";
const chip = "rounded-full border border-border px-2 py-0.5 text-[11px] font-medium text-muted-foreground";

export const NOTE_LIMIT = 200;

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
  // The send field's name is the Browser agent's contract: read only through the adapter.
  const sendState = browserSendState(c);
  const canQueue = sendState !== "unsupported";
  const queued = sendState === "queued";

  async function queueSend() {
    setSending(true);
    try {
      await queueBrowserSend(c.id);
      await onChanged();
    } catch (err) {
      toast.apiError(err, "Couldn't queue that");
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
        <span className={chip} data-testid={`li-state-${c.id}`}>{STATE_LABEL[state]}</span>
      </div>
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
          <p className={`text-xs ${over ? "font-semibold text-amber-600" : "text-muted-foreground"}`}
            data-testid={`note-count-${c.id}`} data-over={over ? "true" : "false"}>
            {count}/{NOTE_LIMIT}
          </p>
          <p className="text-xs text-muted-foreground">LinkedIn connection notes are 200 characters at most</p>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        {canQueue &&
          (queued ? (
            <button type="button" className={primaryBtn} disabled data-testid={`queued-${c.id}`}>
              Queued for your browser
            </button>
          ) : (
            <button type="button" className={primaryBtn} data-testid={`send-browser-${c.id}`}
              disabled={busy || sending || over}
              title={over ? "This connection note is over 200 characters. Shorten it first." : undefined}
              onClick={() => void queueSend()}>
              Use my assistant&apos;s browser
            </button>
          ))}
        <button type="button" className={btn}
          onClick={() => navigator.clipboard.writeText(person.message).then(() => setCopied(true), () => toast.error("Couldn't copy"))}>
          {copied ? "Copied" : "Copy"}
        </button>
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
