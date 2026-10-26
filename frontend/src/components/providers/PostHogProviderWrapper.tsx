"use client";

/**
 * PostHogProviderWrapper — PostHog analytics for the App Router.
 *
 * Responsibilities:
 *   1. Initialise the posthog-js singleton once on the client (useEffect guards
 *      SSR; the "use client" directive ensures this never runs server-side).
 *   2. Mount the PostHogProvider context so any child can call usePostHog().
 *   3. Track SPA route changes as $pageview events via usePathname (path only,
 *      never the query string: it can carry sign-in tokens).
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
import { usePathname } from "next/navigation";
import { useEffect, useSyncExternalStore, Suspense } from "react";

import type { ConsentValue } from "@/lib/consent";
import { getConsent, subscribeConsent } from "@/lib/consent";

// ── URL scrubbing ────────────────────────────────────────────────────────────
// Magic-link and reset tokens travel in the query string, and posthog-js adds
// $current_url / $referrer to EVERY event by itself (pageleave included). So the
// only safe place to strip them is before_send, which sees the final event.

const URL_KEY = /url|referrer|pathname/i;
const ABSOLUTE_URL = /^https?:\/\//i;

function stripQueryAndHash(value: string): string {
  const cut = value.search(/[?#]/);
  return cut === -1 ? value : value.slice(0, cut);
}

function scrubBag<T>(bag: T): T {
  if (!bag || typeof bag !== "object") return bag;
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(bag as Record<string, unknown>)) {
    out[key] =
      typeof value === "string" && (URL_KEY.test(key) || ABSOLUTE_URL.test(value))
        ? stripQueryAndHash(value)
        : value;
  }
  return out as T;
}

/** Remove query strings and hashes from every URL-valued property of an event. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function scrubUrlProperties<T extends Record<string, any> | null>(event: T): T {
  if (!event) return event;
  const copy = { ...event } as Record<string, unknown>;
  for (const field of ["properties", "$set", "$set_once"]) {
    if (copy[field]) copy[field] = scrubBag(copy[field]);
  }
  return copy as T;
}

// ── Init ─────────────────────────────────────────────────────────────────────

/** Idempotent init. Returns true when posthog is loaded and may capture.
 *
 *  This lives OUTSIDE both components on purpose — see the ordering note on
 *  PageViewTracker. Whichever effect runs first initialises; the other one
 *  finds `__loaded` and does nothing.
 */
function ensurePostHogLoaded(): boolean {
  const key = process.env.NEXT_PUBLIC_POSTHOG_KEY;
  if (!key) return false;
  if (posthog.__loaded) return true;

  posthog.init(key, {
    api_host:
      process.env.NEXT_PUBLIC_POSTHOG_HOST ?? "https://eu.i.posthog.com",
    // We fire $pageview manually so route-change events have the correct URL.
    capture_pageview: false,
    capture_pageleave: true,
    // Autocapture sends the text of every clicked element (equality answers,
    // Art. 9 data) linked to the user's email. Funnel events are fired by hand.
    autocapture: false,
    before_send: scrubUrlProperties,
    // Only build profiles for identified users to stay GDPR-friendly.
    person_profiles: "identified_only",
    // Never record sessions: it would capture the user's CV, salary and
    // application content — far beyond what they consented to (fable/05 C3).
    disable_session_recording: true,
    // Belt and braces should recording ever be switched on remotely.
    session_recording: { maskAllInputs: true },
  });
  return true;
}

// ── Page-view tracker ────────────────────────────────────────────────────────
// Separate component, kept inside a <Suspense> boundary (harmless; it used to need one) in
// Next.js App Router (it opts into dynamic rendering).
//
// WHY THIS EFFECT INITIALISES POSTHOG ITSELF — a real production outage of this
// instrument (2026-08-10 → 2026-08-13: $pageleave arriving from /profile on four
// consecutive days with ZERO $pageview; last $pageview of any kind
// 2026-08-09T08:29:48Z).
//
// React runs CHILD effects before PARENT effects. PageViewTracker is a child of
// PostHogProviderWrapper, so this effect fired BEFORE the parent's effect called
// posthog.init() — and a capture before init is a silent no-op in posthog-js.
// Its deps [pathname, searchParams] then never changed, so it never re-ran and
// that page load emitted no pageview at all. Only a client-side route change
// (effect re-runs, init already done) ever produced one — which is why the
// numbers died the moment the one active person stopped navigating and just
// opened /profile directly. $pageleave kept arriving because posthog-js emits
// that one itself, after init.
//
// Doing init here, in the same effect and immediately before the capture,
// removes the ordering assumption entirely instead of trying to satisfy it.
// The consent check is also a REAL gate now: a declined visitor used to still
// reach posthog.capture(), and only posthog-js's uninitialised-call guard kept
// it silent.
function PageViewTracker({ consent }: { consent: ConsentValue | null }) {
  const pathname = usePathname();

  useEffect(() => {
    if (consent !== "accepted") return;
    if (!ensurePostHogLoaded()) return;
    // origin + pathname only: the query string can hold a sign-in token.
    const url = window.location.origin + pathname;
    posthog.capture("$pageview", { $current_url: url });
  }, [consent, pathname]);

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
    const alreadyLoaded = posthog.__loaded;

    if (consent !== "accepted") {
      // Anything that is not "accepted" after having loaded earlier in this
      // session — stop sending and drop the stored id.
      //
      // NOT `consent === "declined"`. Only one non-accepted value opted out, so
      // any other — a withdrawal, a cleared choice, a value added later — left
      // capture RUNNING while the app believed consent was absent. The gate
      // above already says "not accepted"; re-narrowing it underneath is how a
      // consent check ends up narrower than the consent rule.
      //
      // AND `reset()` COMES FIRST. `reset()` clears the SDK's consent state, so
      // opting out and then resetting undoes the opt-out — with
      // `opt_out_capturing_by_default: false` the SDK is free to capture again
      // while the application still reads "declined". Order is the whole fix.
      // (CodeRabbit, PR #387 — Major, and correct.)
      if (alreadyLoaded) {
        posthog.reset();
        posthog.opt_out_capturing();
      }
      return;
    }

    if (alreadyLoaded) {
      posthog.opt_in_capturing(); // re-accepted after a decline
      return;
    }

    // Normally PageViewTracker's effect (a child, so it runs first) has already
    // done this. Kept so the singleton still loads on a page that somehow has
    // no tracker mounted.
    ensurePostHogLoaded();
  }, [consent]);

  return (
    // Pass the singleton as `client` — PostHogProvider reads it for usePostHog().
    <PostHogProvider client={posthog}>
      {/* Suspense kept from when the tracker read useSearchParams() */}
      <Suspense fallback={null}>
        <PageViewTracker consent={consent} />
      </Suspense>
      {children}
    </PostHogProvider>
  );
}
