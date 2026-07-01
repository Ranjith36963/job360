/**
 * instrumentation-client.ts — client-side Sentry initialisation.
 *
 * Introduced in Next.js 15.3 and supported in 16.x. Runs in the browser AFTER
 * the HTML document loads but BEFORE React hydration, which is the ideal time
 * to set up error tracking so nothing slips through the gap.
 *
 * Unlike instrumentation.ts (server), this file does NOT export register().
 * Code at the top level runs once; exported functions get called by the framework.
 *
 * Docs: frontend/node_modules/next/dist/docs/01-app/03-api-reference/03-file-conventions/instrumentation-client.md
 */

import * as Sentry from "@sentry/nextjs";

const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;

if (dsn) {
  Sentry.init({
    dsn,
    tracesSampleRate: process.env.NODE_ENV === "development" ? 1.0 : 0.1,
    integrations: [
      // Session Replay — records the user session for error investigation.
      // Only 10% of normal sessions; 100% of sessions that hit an error.
      Sentry.replayIntegration({
        maskAllText: false,
        blockAllMedia: false,
      }),
    ],
    replaysSessionSampleRate: 0.1,
    replaysOnErrorSampleRate: 1.0,
    ignoreErrors: [
      "ResizeObserver loop limit exceeded",
      "Network request failed",
    ],
  });
}

// Instrument App Router navigations so Sentry can create navigation spans.
// Recommended by @sentry/nextjs for instrumentation-client.ts.
export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
