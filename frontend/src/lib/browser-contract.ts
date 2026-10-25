import { postEmpty, updateContact } from "@/lib/api";
import type { Ask, Contact } from "@/lib/api";

/**
 * The ONE place that knows the browser fields and calls. The Job 360 Browser agent owns this
 * contract (owner 2026-10-25); until its PRs land these names are placeholders. When they
 * land, change the names below and nothing else: every card reads through these helpers.
 * Each field is feature-detected, so nothing shows until the backend sends it.
 */
export const BROWSER_CONTRACT = {
  /** On an ask: true = the action must run in the owner's own Chrome (PC on). */
  ownBrowserField: "needs_own_browser",
  /** On a contact: true = the owner approved a send by the assistant's browser. */
  readyToSendField: "ready_to_send",
  /** On an ask: present (truthy = queued) once the backend supports queueing it for the owner's browser. */
  askBrowserTaskField: "browser_task",
  /** POST, {id} = the ask id. Placeholder until the Browser agent's endpoint lands. */
  queueTaskPath: "/api/asks/{id}/browser-task",
} as const;

/** True only when the backend marks this ask for the owner's own Chrome. Absent = Job360's browser. */
export function needsOwnBrowser(ask: Ask): boolean {
  return (ask as unknown as Record<string, unknown>)[BROWSER_CONTRACT.ownBrowserField] === true;
}

/** "unsupported" until the backend sends the field; then "queued" or "ready". */
export function browserSendState(contact: Contact): "unsupported" | "ready" | "queued" {
  const c = contact as unknown as Record<string, unknown>;
  if (!(BROWSER_CONTRACT.readyToSendField in c)) return "unsupported";
  return c[BROWSER_CONTRACT.readyToSendField] === true ? "queued" : "ready";
}

/** The owner's yes for one send by the assistant's browser. */
export async function queueBrowserSend(contactId: number): Promise<void> {
  await updateContact(contactId, { [BROWSER_CONTRACT.readyToSendField]: true } as never);
}

/** "unsupported" when the ask has no browser_task key; "queued" when it is truthy; else "ready". */
export function browserTaskState(ask: Ask): "unsupported" | "ready" | "queued" {
  const a = ask as unknown as Record<string, unknown>;
  if (!(BROWSER_CONTRACT.askBrowserTaskField in a)) return "unsupported";
  return a[BROWSER_CONTRACT.askBrowserTaskField] ? "queued" : "ready";
}

/** Queue one ask's task for the owner's own browser. */
export async function queueBrowserTask(askId: number): Promise<void> {
  await postEmpty(BROWSER_CONTRACT.queueTaskPath.replace("{id}", String(askId)));
}
