"use client";

/**
 * /sentry-test — dev-only page to confirm Sentry is receiving events.
 *
 * Hit "Throw client error" and then check your Sentry project for the event.
 * This page is intentionally unstyled and should not be linked from the app.
 */

import { useState } from "react";
import * as Sentry from "@sentry/nextjs";

export default function SentryTestPage() {
  const [thrown, setThrown] = useState(false);

  if (thrown) {
    // This throw is caught by the nearest error.tsx / global-error.tsx, and
    // simultaneously captured by the Sentry React error boundary integration.
    throw new Error("Job360 Sentry test error — intentional, safe to ignore");
  }

  function handleManualCapture() {
    Sentry.captureException(
      new Error("Job360 Sentry manual capture test — from captureException")
    );
    alert("Event sent via Sentry.captureException — check your Sentry dashboard.");
  }

  return (
    <div className="mx-auto max-w-md py-16 space-y-4">
      <h1 className="text-xl font-semibold">Sentry Integration Test</h1>
      <p className="text-sm text-muted-foreground">
        These buttons send real events to the configured Sentry DSN. For dev use
        only — do not link from production UI.
      </p>
      <div className="flex gap-3">
        <button
          onClick={() => setThrown(true)}
          className="rounded bg-destructive px-4 py-2 text-sm text-white"
        >
          Throw client error (React boundary)
        </button>
        <button
          onClick={handleManualCapture}
          className="rounded bg-primary px-4 py-2 text-sm text-white"
        >
          Manual captureException
        </button>
      </div>
    </div>
  );
}
