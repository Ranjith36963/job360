/**
 * instrumentation.ts — Next.js server/edge observability hook.
 *
 * Called once when the Next.js server starts. Registers Sentry for the Node.js
 * runtime and the Edge runtime separately so each gets the right SDK internals.
 * Also exports onRequestError so Sentry captures Server Component / Route Handler
 * errors that would otherwise be swallowed.
 *
 * Placed in src/ per the Next.js 16 convention (root or src/ are both valid).
 */

import * as Sentry from "@sentry/nextjs";

export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    await import("./sentry.server.config");
  }

  if (process.env.NEXT_RUNTIME === "edge") {
    await import("./sentry.edge.config");
  }
}

// Captures errors thrown from Server Components, Route Handlers, and middleware.
export const onRequestError = Sentry.captureRequestError;
