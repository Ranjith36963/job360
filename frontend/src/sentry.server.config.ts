/**
 * sentry.server.config.ts — Sentry initialisation for the Node.js runtime.
 *
 * Imported dynamically from instrumentation.ts when NEXT_RUNTIME === "nodejs".
 * If NEXT_PUBLIC_SENTRY_DSN is not set the init is skipped so local dev without
 * the .env.local file never crashes.
 */

import * as Sentry from "@sentry/nextjs";

const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;

if (dsn) {
  Sentry.init({
    dsn,
    // 100% in dev so every error appears immediately; 10% in prod to stay within limits.
    tracesSampleRate: process.env.NODE_ENV === "development" ? 1.0 : 0.1,
    // Reduce noise from known benign errors
    ignoreErrors: [
      "ResizeObserver loop limit exceeded",
      "Network request failed",
    ],
  });
}
