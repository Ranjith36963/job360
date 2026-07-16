"use client";

/**
 * PostHogProviderWrapper — PostHog analytics for the App Router.
 *
 * Responsibilities:
 *   1. Initialise the posthog-js singleton once on the client (useEffect guards
 *      SSR; the "use client" directive ensures this never runs server-side).
 *   2. Mount the PostHogProvider context so any child can call usePostHog().
 *   3. Track SPA route changes as $pageview events via usePathname +
 *      useSearchParams (both in a <Suspense> boundary as required by Next.js).
 *
 * Identify / reset are handled by AuthProvider — it imports posthog-js directly
 * and calls posthog.identify() on login and posthog.reset() on logout. The
 * singleton means both files share the same instance.
 *
 * If NEXT_PUBLIC_POSTHOG_KEY is not set the init is skipped and all calls are
 * no-ops (posthog-js has built-in guards for uninitialized calls).
 */

import posthog from "posthog-js";
import { PostHogProvider } from "posthog-js/react";
import { usePathname, useSearchParams } from "next/navigation";
import { useEffect, useSyncExternalStore, Suspense } from "react";

import { getConsent, subscribeConsent } from "@/lib/consent";

// ── Page-view tracker ────────────────────────────────────────────────────────
// Separate component because useSearchParams requires a <Suspense> boundary in
// Next.js App Router (it opts into dynamic rendering).

function PageViewTracker() {
  const pathname = usePathname();
  const searchParams = useSearchParams();

  useEffect(() => {
    const qs = searchParams.toString();
    const url =
      window.location.origin + (qs ? `${pathname}?${qs}` : pathname);
    posthog.capture("$pageview", { $current_url: url });
  }, [pathname, searchParams]);

  return null;
}

// ── Provider ─────────────────────────────────────────────────────────────────

export function PostHogProviderWrapper({
  children,
}: {
  children: React.ReactNode;
}) {
  // Consent gate (docs/fable/05 C3). PostHog used to init on load — i.e. before
  // the user agreed to anything, which UK GDPR/PECR does not allow. Now nothing
  // loads until the choice is explicitly "accepted"; no choice yet = no tracking.
  // Consent is an external store (localStorage + window event), so subscribe via
  // useSyncExternalStore. Server snapshot = null → never init during SSR.
  const consent = useSyncExternalStore(subscribeConsent, getConsent, () => null);

  useEffect(() => {
    const key = process.env.NEXT_PUBLIC_POSTHOG_KEY;
    if (!key) return;

    if (consent !== "accepted") {
      // Declined (or undecided) after having loaded earlier in this session —
      // stop sending and drop the stored id.
      if (posthog.__loaded && consent === "declined") {
        posthog.opt_out_capturing();
        posthog.reset();
      }
      return;
    }

    if (posthog.__loaded) {
      posthog.opt_in_capturing(); // re-accepted after a decline
      return;
    }

    posthog.init(key, {
      api_host:
        process.env.NEXT_PUBLIC_POSTHOG_HOST ?? "https://eu.i.posthog.com",
      // We fire $pageview manually so route-change events have the correct URL.
      capture_pageview: false,
      capture_pageleave: true,
      // Only build profiles for identified users to stay GDPR-friendly.
      person_profiles: "identified_only",
      // Never record sessions: it would capture the user's CV, salary and
      // application content — far beyond what they consented to (fable/05 C3).
      disable_session_recording: true,
    });
  }, [consent]);

  return (
    // Pass the singleton as `client` — PostHogProvider reads it for usePostHog().
    <PostHogProvider client={posthog}>
      {/* Suspense required because PageViewTracker calls useSearchParams() */}
      <Suspense fallback={null}>
        <PageViewTracker />
      </Suspense>
      {children}
    </PostHogProvider>
  );
}
