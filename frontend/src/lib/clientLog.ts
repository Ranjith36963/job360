/**
 * Frontend → server log bridge (full-lifecycle logging piece D).
 *
 * POSTs UI errors/events to `/api/client-log` so they land in the server's
 * `data/logs/` instead of vanishing when the user closes the tab. Best-effort
 * and fire-and-forget: it must NEVER throw or surface an error to the user.
 */
const API = process.env.NEXT_PUBLIC_API_URL ?? "";

export type ClientLogLevel = "error" | "warning" | "info";

export interface ClientLogPayload {
  message: string;
  level?: ClientLogLevel;
  stack?: string;
  context?: string;
}

export function reportClientLog(payload: ClientLogPayload): void {
  try {
    const body = JSON.stringify({
      level: payload.level ?? "error",
      message: payload.message.slice(0, 2000),
      stack: payload.stack?.slice(0, 4000),
      context: payload.context,
      url: typeof window !== "undefined" ? window.location.href : undefined,
    });
    void fetch(`${API}/api/client-log`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      keepalive: true, // still sends while the page is unloading
      body,
    }).catch(() => {
      /* logging must never surface an error to the user */
    });
  } catch {
    /* swallow — never let logging throw */
  }
}
