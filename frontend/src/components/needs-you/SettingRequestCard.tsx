"use client";

import { useState } from "react";
import { toast } from "sonner";
import { confirmSettingRequest, declineSettingRequest } from "@/lib/api";
import type { SettingRequest } from "@/lib/api";
import { relativeTime } from "@/lib/utils";

// "Waiting for your OK" (S2, owner decision 2026-10-08). An assistant asked for a
// change that gives IT more freedom. Nothing is applied until the user clicks
// Confirm here; "Don't change" closes it. Every value is rendered as a React
// text node, never as HTML. The full settings page is S5 — this card only
// explains the one change in plain words.

const APPLY_MODES: Record<string, string> = {
  ask_each: "Ask me about each job",
  apply_all: "Apply to every job I bring, without asking",
  selective_above_score: "Apply on its own above the score line",
};
const SUBMIT_MODES: Record<string, string> = {
  confirm: "I check every application before it is sent",
  auto_when_sure: "Send applications on its own when it is sure",
};
const INBOX_MODES: Record<string, string> = {
  auto: "Read my inbox and send outreach emails on its own",
  scheduled: "Read my inbox and send outreach emails on its own",
  ask: "Ask me before each inbox check",
  paused: "Stop checking my inbox",
};

/** What a request changes, in plain words: a title and the new value. */
export function describeSettingRequest(path: string, value: unknown): { title: string; change: string } {
  switch (path) {
    case "assistant_settings.apply_mode":
      return { title: "How it applies", change: APPLY_MODES[String(value)] ?? "Change how it applies" };
    case "assistant_settings.apply_min_score":
      return { title: "Score line", change: `Only apply alone at ${String(value)} or above` };
    case "assistant_settings.submit_mode":
      return { title: "Sending", change: SUBMIT_MODES[String(value)] ?? "Change how it sends" };
    case "assistant_settings.daily_cap":
      return {
        title: "Daily limit",
        change: value === null || value === undefined ? "No daily limit" : `Up to ${String(value)} applications a day`,
      };
    case "assistant_settings.paused_until":
      if (value === "" || value === null || value === undefined) {
        return { title: "Pause", change: "Start applying again" };
      }
      return {
        title: "Pause",
        change: value === "until_resumed" ? "Paused until you resume" : `Paused until ${String(value)}`,
      };
    case "preferences.daily_check":
      return { title: "Your inbox", change: INBOX_MODES[String(value)] ?? "Change the inbox check" };
    default:
      return { title: "A setting", change: "A change to your assistant settings" };
  }
}

const confirmBtn =
  "rounded-md bg-primary px-2.5 py-1 text-xs font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50";
const cancelBtn =
  "rounded-md border border-border px-2.5 py-1 text-xs font-medium text-muted-foreground hover:text-foreground disabled:opacity-50";

export function SettingRequestCard({
  request,
  onChanged,
}: {
  request: SettingRequest;
  onChanged: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const { title, change } = describeSettingRequest(request.path, request.value);

  async function run(fn: () => Promise<unknown>, failMsg: string) {
    setBusy(true);
    try {
      await fn();
      await onChanged();
    } catch {
      toast.error(failMsg);
    } finally {
      setBusy(false);
    }
  }

  return (
    <li
      data-testid={`setting-request-${request.id}`}
      className="flex flex-col gap-3 rounded-xl border border-border bg-card p-5 shadow-card"
    >
      <div className="flex flex-col gap-1.5">
        <p className="font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint">{title}</p>
        <p className="whitespace-pre-wrap font-heading text-xl leading-snug" data-testid="setting-request-change">
          {change}
        </p>
        <p className="text-[12.5px] text-muted-foreground">
          asked by {request.requested_by} ·{" "}
          <span className="font-mono">{relativeTime(request.requested_at)}</span>
        </p>
        <p className="text-sm text-muted-foreground">
          Nothing changes until you confirm. Your assistant cannot confirm this for you.
        </p>
      </div>
      <div className="flex items-center gap-2">
        <button
          type="button"
          data-testid="setting-request-confirm"
          disabled={busy}
          onClick={() =>
            void run(
              () => confirmSettingRequest(request.id),
              "Could not confirm. It may have expired — ask your assistant to send it again.",
            )
          }
          className={confirmBtn}
        >
          {busy ? "Working…" : "Confirm"}
        </button>
        <button
          type="button"
          data-testid="setting-request-decline"
          disabled={busy}
          onClick={() => void run(() => declineSettingRequest(request.id), "Could not close this request.")}
          className={cancelBtn}
        >
          Don&apos;t change
        </button>
      </div>
    </li>
  );
}
