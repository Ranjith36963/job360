import { requestJson } from "@/lib/api";
import type { Ask, Contact, ReadyCardData } from "@/lib/api";
import type { components } from "@/lib/api-types";

export type OwnBrowserQueue = components["schemas"]["QueueOut"];

/**
 * The ONE place that knows the browser fields and calls (the Job 360 Browser agent's contract,
 * fixed 2026-10-25). None of these fields are in api-types.ts yet, so each is feature-detected
 * and validated: nothing renders until the backend sends a well-formed value.
 */
export const BROWSER_CONTRACT = {
  /** BrowserLabelOut on an ask. */
  askField: "browser",
  /** BrowserLabelOut on a ready-to-send card. */
  readyField: "browser",
  /** BrowserLabelOut on a contact (how its message will be sent). */
  contactField: "send_browser",
  /** On a contact: the owner's-browser send task. */
  taskField: "own_browser_task",
  /** POST {message_id, decision}; {id} = the contact id. */
  ownBrowserSendPath: "/api/contacts/{id}/own-browser-send",
  runNowPath: "/api/own-browser/run-now",
  queuePath: "/api/own-browser/queue",
} as const;

export type BrowserKind = "job360" | "own_browser" | "human";
export type BrowserLabel = { kind: BrowserKind; label: string; reason: string };

const KINDS: readonly string[] = ["job360", "own_browser", "human"];

/** Reads obj[field] as a BrowserLabelOut; null when absent or malformed (unknown kind, non-string text). */
export function browserLabel(obj: unknown, field: string): BrowserLabel | null {
  if (!obj || typeof obj !== "object") return null;
  const v = (obj as Record<string, unknown>)[field];
  if (!v || typeof v !== "object") return null;
  const { kind, label, reason } = v as Record<string, unknown>;
  if (typeof kind !== "string" || !KINDS.includes(kind)) return null;
  if (typeof label !== "string") return null;
  return { kind: kind as BrowserKind, label, reason: typeof reason === "string" ? reason : "" };
}

export const askBrowser = (ask: Ask): BrowserLabel | null => browserLabel(ask, BROWSER_CONTRACT.askField);
export const readyBrowser = (card: ReadyCardData): BrowserLabel | null =>
  browserLabel(card, BROWSER_CONTRACT.readyField);
export const contactSendBrowser = (c: Contact): BrowserLabel | null =>
  browserLabel(c, BROWSER_CONTRACT.contactField);

/** True only when the backend labels this ask for the owner's own browser. Absent = Job360's browser. */
export function needsOwnBrowser(ask: Ask): boolean {
  return askBrowser(ask)?.kind === "own_browser";
}

const OWNER_STEP_KINDS: readonly string[] = ["captcha", "login", "upload", "safety"];

/** The label an ask shows: the backend's own label wins; a blocker the backend left unlabelled
 *  (captcha, login, upload, safety) is the owner's step, so it reads "You". Anything else: null. */
export function effectiveAskBrowser(ask: Ask, kind: string): BrowserLabel | null {
  const sent = askBrowser(ask);
  if (sent) return sent;
  return OWNER_STEP_KINDS.includes(kind) ? { kind: "human", label: "You", reason: "" } : null;
}

export type OwnBrowserTaskState = "waiting_on_you" | "queued" | "in_progress" | "declined";
export type OwnBrowserTask = {
  state: OwnBrowserTaskState;
  message_id: number;
  approved_at: string | null;
  claim_until: string | null;
};
const TASK_STATES: readonly string[] = ["waiting_on_you", "queued", "in_progress", "declined"];

/** The contact's own-browser send task, or null when absent or malformed. */
export function ownBrowserTask(c: Contact): OwnBrowserTask | null {
  const v = (c as unknown as Record<string, unknown>)[BROWSER_CONTRACT.taskField];
  if (!v || typeof v !== "object") return null;
  const t = v as Record<string, unknown>;
  if (typeof t.state !== "string" || !TASK_STATES.includes(t.state)) return null;
  if (typeof t.message_id !== "number") return null;
  return {
    state: t.state as OwnBrowserTaskState,
    message_id: t.message_id,
    approved_at: typeof t.approved_at === "string" ? t.approved_at : null,
    claim_until: typeof t.claim_until === "string" ? t.claim_until : null,
  };
}

/** The owner's yes (approve) or no (decline) to one send in their own browser. */
export async function decideOwnBrowserSend(
  contactId: number,
  messageId: number,
  decision: "approve" | "decline",
): Promise<void> {
  await requestJson(BROWSER_CONTRACT.ownBrowserSendPath.replace("{id}", String(contactId)), "POST", {
    message_id: messageId,
    decision,
  });
}

/** Open the queue for the owner's own browser for the next hour. Throws ApiError (404/405 = the backend has no run-now yet). */
export async function runOwnBrowserNow(): Promise<void> {
  await requestJson(BROWSER_CONTRACT.runNowPath, "POST");
}

/** The own-browser queue (generated QueueOut); null when the call fails. */
export async function getOwnBrowserQueue(): Promise<OwnBrowserQueue | null> {
  try {
    const r = await requestJson<OwnBrowserQueue>(BROWSER_CONTRACT.queuePath, "GET");
    return r && typeof r === "object" && Array.isArray(r.tasks) && typeof r.count === "number" ? r : null;
  } catch {
    return null;
  }
}

/** How many tasks wait for the owner's browser; null when the endpoint fails or answers an unknown shape. */
export async function ownBrowserQueueCount(): Promise<number | null> {
  try {
    const r = await requestJson<unknown>(BROWSER_CONTRACT.queuePath, "GET");
    if (Array.isArray(r)) return r.length;
    if (r && typeof r === "object") {
      const o = r as Record<string, unknown>;
      if (Array.isArray(o.items)) return o.items.length;
      if (typeof o.count === "number" && Number.isFinite(o.count)) return o.count;
    }
    return null;
  } catch {
    return null;
  }
}
