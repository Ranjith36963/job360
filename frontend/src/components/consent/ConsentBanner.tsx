"use client";

/**
 * ConsentBanner — analytics consent prompt (docs/fable/05 C3).
 *
 * Shown only while the user has made NO choice. Until they accept, PostHog is
 * never initialised (see PostHogProviderWrapper), so this is a real gate rather
 * than cosmetic theatre.
 *
 * PECR/ICO expectations this deliberately meets:
 *   - "Decline" is exactly as prominent and as easy as "Accept" (one click, same
 *     visual weight). No dark-pattern where reject is buried.
 *   - Nothing is set before the choice — no pre-ticked "accept" default.
 *   - The choice is remembered so we don't nag on every page.
 */

import { useSyncExternalStore } from "react";

import { Button } from "@/components/ui/button";
import { getConsent, setConsent, subscribeConsent } from "@/lib/consent";

// Server-snapshot sentinel: on SSR/first hydration paint we don't know the
// choice yet, so render NOTHING (never flash the banner at someone who already
// decided). After mount the real snapshot (getConsent) takes over.
const UNHYDRATED = "unhydrated" as const;

export function ConsentBanner() {
  // Consent lives in an external store (localStorage + a window event), so
  // useSyncExternalStore is the idiomatic subscription — no setState-in-effect.
  const consent = useSyncExternalStore(
    subscribeConsent,
    getConsent,
    () => UNHYDRATED,
  );

  // Show only to a hydrated visitor with NO choice yet.
  if (consent !== null) return null;

  return (
    <div
      role="dialog"
      aria-modal="false"
      aria-label="Analytics cookie consent"
      className="fixed inset-x-0 bottom-0 z-50 border-t bg-background/95 p-4 backdrop-blur supports-[backdrop-filter]:bg-background/80"
    >
      <div className="mx-auto flex max-w-4xl flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-sm text-muted-foreground">
          We&apos;d like to use analytics to understand how Job360 is used and improve
          it. This is optional — Job360 works exactly the same either way, and we
          never record your session or your CV content.
        </p>
        <div className="flex shrink-0 gap-2">
          {/* Same size + weight as Accept: rejecting must be no harder. */}
          <Button
            variant="outline"
            size="sm"
            onClick={() => setConsent("declined")}
          >
            Decline
          </Button>
          <Button size="sm" onClick={() => setConsent("accepted")}>
            Accept
          </Button>
        </div>
      </div>
    </div>
  );
}
