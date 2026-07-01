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
import { useEffect, Suspense } from "react";

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
  useEffect(() => {
    const key = process.env.NEXT_PUBLIC_POSTHOG_KEY;
    // Skip if no key or already initialised (module-level singleton guard).
    if (!key || posthog.__loaded) return;

    posthog.init(key, {
      api_host:
        process.env.NEXT_PUBLIC_POSTHOG_HOST ?? "https://eu.i.posthog.com",
      // We fire $pageview manually so route-change events have the correct URL.
      capture_pageview: false,
      capture_pageleave: true,
      // Only build profiles for identified users to stay GDPR-friendly.
      person_profiles: "identified_only",
    });
  }, []);

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
