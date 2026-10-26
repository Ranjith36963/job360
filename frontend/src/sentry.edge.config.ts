/**
 * sentry.edge.config.ts — Sentry initialisation for the Edge runtime.
 *
 * Imported dynamically from instrumentation.ts when NEXT_RUNTIME === "edge".
 * Edge runtime does not support all Node APIs; keep this config minimal.
 */

import * as Sentry from "@sentry/nextjs";

import { scrubSentryEvent } from "@/lib/sentryScrub";

const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;

if (dsn) {
  Sentry.init({
    dsn,
    beforeSend: (event) => scrubSentryEvent(event),
    beforeSendTransaction: (event) => scrubSentryEvent(event),
    tracesSampleRate: process.env.NODE_ENV === "development" ? 1.0 : 0.1,
  });
}
