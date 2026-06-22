"use client";

import { useEffect } from "react";

import { reportClientLog } from "@/lib/clientLog";

/**
 * Mounts global browser error listeners (uncaught errors + unhandled promise
 * rejections) and forwards them to the server log bridge. Renders nothing.
 *
 * This is the "uncaught + explicit events" capture strategy: we do NOT mirror
 * every console.error (too noisy) — only genuinely uncaught failures plus the
 * explicit reports from the error boundary.
 */
export function ClientErrorReporter() {
  useEffect(() => {
    const onError = (e: ErrorEvent) => {
      reportClientLog({
        message: e.message || "window.onerror",
        stack: e.error instanceof Error ? e.error.stack : undefined,
        context: "window.onerror",
      });
    };
    const onRejection = (e: PromiseRejectionEvent) => {
      const reason: unknown = e.reason;
      reportClientLog({
        message:
          reason instanceof Error ? reason.message : String(reason ?? "unhandledrejection"),
        stack: reason instanceof Error ? reason.stack : undefined,
        context: "unhandledrejection",
      });
    };
    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onRejection);
    return () => {
      window.removeEventListener("error", onError);
      window.removeEventListener("unhandledrejection", onRejection);
    };
  }, []);

  return null;
}
